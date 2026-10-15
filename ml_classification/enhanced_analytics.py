#!/usr/bin/env python3
"""
Enhanced Sedentary Behavior Analytics System
LOINC Code: 87705-0 (Sedentary activity 24 hour)

Features:
- User-specific daily/weekly/monthly analytics
- KMeans clustering for pattern detection
- Adaptive threshold suggestions
- FHIR-compliant observations
- Trend analysis and predictions
"""

import psycopg2
import pandas as pd
from sklearn.cluster import KMeans
from datetime import datetime, timedelta
import json
import os
from typing import Dict, List, Optional

# Environment variables - Database
DB_URL = os.environ.get("DATABASE_URL")
if not DB_URL:
    raise ValueError("DATABASE_URL environment variable must be set")

# Environment variables - Activity Thresholds
THRESH_FIDGET = float(os.environ.get("THRESH_FIDGET", "0.020"))
THRESH_ACTIVE = float(os.environ.get("THRESH_ACTIVE", "0.040"))

# Environment variables - ML Configuration
ML_KMEANS_CLUSTERS = int(os.environ.get("ML_KMEANS_CLUSTERS", "3"))
ML_KMEANS_RANDOM_STATE = int(os.environ.get("ML_KMEANS_RANDOM_STATE", "42"))
ML_KMEANS_N_INIT = int(os.environ.get("ML_KMEANS_N_INIT", "10"))
ML_MIN_SAMPLES_FOR_CLUSTERING = int(os.environ.get("ML_MIN_SAMPLES_FOR_CLUSTERING", "100"))
ML_SAMPLES_PER_MINUTE = int(os.environ.get("ML_SAMPLES_PER_MINUTE", "600"))

# Environment variables - LOINC / FHIR Configuration
LOINC_CODE = os.environ.get("LOINC_CODE", "87705-0")
LOINC_DISPLAY = os.environ.get("LOINC_DISPLAY", "Sedentary activity 24 hour")


class SedentaryAnalytics:
    """ML-powered sedentary behavior analytics engine"""

    def __init__(self, db_url: str = DB_URL):
        self.db_url = db_url
        self.conn = None

    def connect(self):
        """Establish database connection"""
        try:
            self.conn = psycopg2.connect(self.db_url)
            print("Connected to database")
        except Exception as e:
            print(f"Database connection error: {e}")
            raise

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            print("Database connection closed")

    def get_active_users(self) -> List[str]:
        """Get list of users with data in the last 24 hours"""
        query = """
            SELECT DISTINCT u.user_id, u.name, u.email
            FROM users u
            INNER JOIN sedentary_log sl ON true
            WHERE sl.created_at > NOW() - INTERVAL '24 HOURS'
        """
        df = pd.read_sql(query, self.conn)

        # If no users exist or no data, return empty list
        if df.empty:
            # Check if there's ANY data (for single-user setups)
            check_query = "SELECT COUNT(*) as cnt FROM sedentary_log WHERE created_at > NOW() - INTERVAL '24 HOURS'"
            result = pd.read_sql(check_query, self.conn)
            if result.iloc[0]['cnt'] > 0:
                # Single user mode - get first user or create default
                user_query = "SELECT user_id FROM users LIMIT 1"
                user_df = pd.read_sql(user_query, self.conn)
                if not user_df.empty:
                    return [str(user_df.iloc[0]['user_id'])]
            return []

        return df['user_id'].astype(str).tolist()

    def load_user_data(self, user_id: Optional[str], hours: int = 24) -> pd.DataFrame:
        """
        Load sensor data for a specific user or all data if user_id is None

        Args:
            user_id: UUID of user, or None for all data
            hours: Number of hours to look back

        Returns:
            DataFrame with columns: created_at, acceleration_val, state, timer_seconds
        """
        query = f"""
            SELECT
                created_at,
                acceleration_val,
                state,
                timer_seconds
            FROM sedentary_log
            WHERE created_at > NOW() - INTERVAL '{hours} HOURS'
            ORDER BY created_at ASC
        """

        df = pd.read_sql(query, self.conn)

        if df.empty:
            print(f"No data found for {'user ' + user_id if user_id else 'any user'} in last {hours} hours")
        else:
            print(f"Loaded {len(df)} data points for {'user ' + user_id if user_id else 'all users'}")

        return df

    def calculate_basic_stats(self, df: pd.DataFrame) -> Dict:
        """Calculate basic activity statistics"""
        if df.empty:
            return {
                'sedentary_minutes': 0,
                'fidget_minutes': 0,
                'active_minutes': 0,
                'total_minutes': 0,
                'sedentary_percentage': 0,
                'active_percentage': 0,
                'dominant_state': 'UNKNOWN',
                'activity_score': 0,
                'alert_count': 0,
                'longest_sedentary_period': 0
            }

        # Count states based on acceleration thresholds
        active_count = len(df[df['acceleration_val'] > THRESH_ACTIVE])
        fidget_count = len(df[(df['acceleration_val'] > THRESH_FIDGET) &
                               (df['acceleration_val'] <= THRESH_ACTIVE)])
        sedentary_count = len(df[df['acceleration_val'] <= THRESH_FIDGET])

        # Convert to minutes using configurable sample rate
        sedentary_minutes = round(sedentary_count / ML_SAMPLES_PER_MINUTE, 2)
        fidget_minutes = round(fidget_count / ML_SAMPLES_PER_MINUTE, 2)
        active_minutes = round(active_count / ML_SAMPLES_PER_MINUTE, 2)
        total_minutes = sedentary_minutes + fidget_minutes + active_minutes

        # Calculate percentages
        sedentary_pct = (sedentary_minutes / total_minutes * 100) if total_minutes > 0 else 0
        active_pct = ((active_minutes + fidget_minutes) / total_minutes * 100) if total_minutes > 0 else 0

        # Activity score (0-100, higher is better)
        activity_score = int(active_pct) if total_minutes > 0 else 0

        # Determine dominant state
        if active_minutes + fidget_minutes > sedentary_minutes:
            dominant_state = "ACTIVE"
        else:
            dominant_state = "SEDENTARY"

        # Count alerts (timer >= 1200 seconds = 20 minutes)
        if 'timer_seconds' in df.columns:
            alert_count = len(df[df['timer_seconds'] >= 1200])
            longest_period = int(df['timer_seconds'].max()) if not df['timer_seconds'].empty else 0
        else:
            alert_count = 0
            longest_period = 0

        return {
            'sedentary_minutes': sedentary_minutes,
            'fidget_minutes': fidget_minutes,
            'active_minutes': active_minutes,
            'total_minutes': total_minutes,
            'sedentary_percentage': round(sedentary_pct, 2),
            'active_percentage': round(active_pct, 2),
            'dominant_state': dominant_state,
            'activity_score': activity_score,
            'alert_count': alert_count,
            'longest_sedentary_period': longest_period
        }

    def perform_kmeans_analysis(self, df: pd.DataFrame) -> Dict:
        """
        Perform KMeans clustering to detect activity patterns

        Returns:
            Dictionary with cluster centers and suggested thresholds
        """
        if df.empty or len(df) < ML_MIN_SAMPLES_FOR_CLUSTERING:
            print(f"Insufficient data for KMeans analysis (need {ML_MIN_SAMPLES_FOR_CLUSTERING}+ samples)")
            return {
                'patterns': None,
                'suggested_fidget_threshold': THRESH_FIDGET,
                'suggested_active_threshold': THRESH_ACTIVE
            }

        try:
            # Prepare data for clustering
            X = df[['acceleration_val']].values

            # KMeans with configurable clusters
            kmeans = KMeans(
                n_clusters=ML_KMEANS_CLUSTERS,
                random_state=ML_KMEANS_RANDOM_STATE,
                n_init=ML_KMEANS_N_INIT
            )
            labels = kmeans.fit_predict(X)
            centers = sorted(kmeans.cluster_centers_.flatten())

            print(f"Detected cluster centers: {[f'{c:.4f}' for c in centers]}")

            # Calculate suggested thresholds (midpoints between clusters)
            suggested_fidget = (centers[0] + centers[1]) / 2
            suggested_active = (centers[1] + centers[2]) / 2

            # Count samples in each cluster
            cluster_counts = pd.Series(labels).value_counts().to_dict()

            patterns = {
                'cluster_centers': [float(c) for c in centers],
                'cluster_distribution': cluster_counts,
                'silhouette_score': None  # Could add sklearn.metrics.silhouette_score
            }

            return {
                'patterns': patterns,
                'suggested_fidget_threshold': round(suggested_fidget, 4),
                'suggested_active_threshold': round(suggested_active, 4)
            }

        except Exception as e:
            print(f" KMeans analysis failed: {e}")
            return {
                'patterns': None,
                'suggested_fidget_threshold': THRESH_FIDGET,
                'suggested_active_threshold': THRESH_ACTIVE
            }

    def save_daily_summary(self, user_id: Optional[str], date: datetime.date, stats: Dict, patterns: Dict):
        """Save daily activity summary to database"""
        try:
            cursor = self.conn.cursor()

            # If no user_id, use NULL (for single-user setups)
            user_id_param = user_id if user_id else None

            cursor.execute("""
                INSERT INTO activity_summary (
                    user_id, date, period_type,
                    sedentary_minutes, fidget_minutes, active_minutes, total_minutes,
                    sedentary_percentage, active_percentage,
                    dominant_state, activity_score,
                    alert_count, longest_sedentary_period,
                    detected_patterns, suggested_fidget_threshold, suggested_active_threshold
                )
                VALUES (%s, %s, 'daily', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, date, period_type)
                DO UPDATE SET
                    sedentary_minutes = EXCLUDED.sedentary_minutes,
                    fidget_minutes = EXCLUDED.fidget_minutes,
                    active_minutes = EXCLUDED.active_minutes,
                    total_minutes = EXCLUDED.total_minutes,
                    sedentary_percentage = EXCLUDED.sedentary_percentage,
                    active_percentage = EXCLUDED.active_percentage,
                    dominant_state = EXCLUDED.dominant_state,
                    activity_score = EXCLUDED.activity_score,
                    alert_count = EXCLUDED.alert_count,
                    longest_sedentary_period = EXCLUDED.longest_sedentary_period,
                    detected_patterns = EXCLUDED.detected_patterns,
                    suggested_fidget_threshold = EXCLUDED.suggested_fidget_threshold,
                    suggested_active_threshold = EXCLUDED.suggested_active_threshold,
                    updated_at = NOW()
            """, (
                user_id_param, date,
                stats['sedentary_minutes'], stats['fidget_minutes'],
                stats['active_minutes'], stats['total_minutes'],
                stats['sedentary_percentage'], stats['active_percentage'],
                stats['dominant_state'], stats['activity_score'],
                stats['alert_count'], stats['longest_sedentary_period'],
                json.dumps(patterns['patterns']),
                patterns['suggested_fidget_threshold'],
                patterns['suggested_active_threshold']
            ))

            self.conn.commit()
            print(f"Daily summary saved for {'user ' + user_id if user_id else 'default user'} on {date}")
            print(f"   Activity Score: {stats['activity_score']}/100")
            print(f"   Sedentary: {stats['sedentary_minutes']:.1f} min ({stats['sedentary_percentage']:.1f}%)")
            print(f"   Active: {stats['active_minutes']:.1f} min ({stats['active_percentage']:.1f}%)")

        except Exception as e:
            print(f"Failed to save daily summary: {e}")
            self.conn.rollback()

    def calculate_weekly_summary(self, user_id: Optional[str], week_start: datetime.date):
        """Aggregate weekly statistics from daily summaries"""
        try:
            cursor = self.conn.cursor()

            # Query daily summaries for the week
            week_end = week_start + timedelta(days=6)

            user_filter = "AND user_id = %s" if user_id else "AND user_id IS NULL"
            params = (week_start, week_end, user_id) if user_id else (week_start, week_end)

            cursor.execute(f"""
                SELECT
                    AVG(sedentary_minutes) as avg_sedentary,
                    AVG(fidget_minutes) as avg_fidget,
                    AVG(active_minutes) as avg_active,
                    AVG(activity_score) as avg_score,
                    SUM(alert_count) as total_alerts,
                    MAX(longest_sedentary_period) as max_sedentary,
                    MODE() WITHIN GROUP (ORDER BY dominant_state) as dominant_state
                FROM activity_summary
                WHERE period_type = 'daily'
                  AND date BETWEEN %s AND %s
                  {user_filter}
            """ + user_filter, params)

            result = cursor.fetchone()

            if result and result[0] is not None:
                stats = {
                    'sedentary_minutes': round(result[0], 2),
                    'fidget_minutes': round(result[1], 2),
                    'active_minutes': round(result[2], 2),
                    'total_minutes': round(result[0] + result[1] + result[2], 2),
                    'sedentary_percentage': round(result[0] / (result[0] + result[1] + result[2]) * 100, 2),
                    'active_percentage': round((result[1] + result[2]) / (result[0] + result[1] + result[2]) * 100, 2),
                    'dominant_state': result[6] or 'UNKNOWN',
                    'activity_score': int(result[3]) if result[3] else 0,
                    'alert_count': result[4] or 0,
                    'longest_sedentary_period': result[5] or 0
                }

                # Save weekly summary
                cursor.execute("""
                    INSERT INTO activity_summary (
                        user_id, date, period_type,
                        sedentary_minutes, fidget_minutes, active_minutes, total_minutes,
                        sedentary_percentage, active_percentage,
                        dominant_state, activity_score,
                        alert_count, longest_sedentary_period
                    )
                    VALUES (%s, %s, 'weekly', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, date, period_type)
                    DO UPDATE SET
                        sedentary_minutes = EXCLUDED.sedentary_minutes,
                        fidget_minutes = EXCLUDED.fidget_minutes,
                        active_minutes = EXCLUDED.active_minutes,
                        total_minutes = EXCLUDED.total_minutes,
                        sedentary_percentage = EXCLUDED.sedentary_percentage,
                        active_percentage = EXCLUDED.active_percentage,
                        dominant_state = EXCLUDED.dominant_state,
                        activity_score = EXCLUDED.activity_score,
                        alert_count = EXCLUDED.alert_count,
                        longest_sedentary_period = EXCLUDED.longest_sedentary_period,
                        updated_at = NOW()
                """, (
                    user_id, week_start,
                    stats['sedentary_minutes'], stats['fidget_minutes'],
                    stats['active_minutes'], stats['total_minutes'],
                    stats['sedentary_percentage'], stats['active_percentage'],
                    stats['dominant_state'], stats['activity_score'],
                    stats['alert_count'], stats['longest_sedentary_period']
                ))

                self.conn.commit()
                print(f"Weekly summary saved for week starting {week_start}")

        except Exception as e:
            print(f"Failed to calculate weekly summary: {e}")
            self.conn.rollback()

    def calculate_monthly_summary(self, user_id: Optional[str], month_start: datetime.date):
        """Aggregate monthly statistics from daily summaries"""
        try:
            cursor = self.conn.cursor()

            # Calculate last day of month
            if month_start.month == 12:
                month_end = month_start.replace(day=31)
            else:
                next_month = month_start.replace(month=month_start.month + 1, day=1)
                month_end = next_month - timedelta(days=1)

            user_filter = "AND user_id = %s" if user_id else "AND user_id IS NULL"
            params = (month_start, month_end, user_id) if user_id else (month_start, month_end)

            cursor.execute(f"""
                SELECT
                    AVG(sedentary_minutes) as avg_sedentary,
                    AVG(fidget_minutes) as avg_fidget,
                    AVG(active_minutes) as avg_active,
                    AVG(activity_score) as avg_score,
                    SUM(alert_count) as total_alerts,
                    MAX(longest_sedentary_period) as max_sedentary,
                    MODE() WITHIN GROUP (ORDER BY dominant_state) as dominant_state
                FROM activity_summary
                WHERE period_type = 'daily'
                  AND date BETWEEN %s AND %s
                  {user_filter}
            """ + user_filter, params)

            result = cursor.fetchone()

            if result and result[0] is not None:
                stats = {
                    'sedentary_minutes': round(result[0], 2),
                    'fidget_minutes': round(result[1], 2),
                    'active_minutes': round(result[2], 2),
                    'total_minutes': round(result[0] + result[1] + result[2], 2),
                    'sedentary_percentage': round(result[0] / (result[0] + result[1] + result[2]) * 100, 2),
                    'active_percentage': round((result[1] + result[2]) / (result[0] + result[1] + result[2]) * 100, 2),
                    'dominant_state': result[6] or 'UNKNOWN',
                    'activity_score': int(result[3]) if result[3] else 0,
                    'alert_count': result[4] or 0,
                    'longest_sedentary_period': result[5] or 0
                }

                # Save monthly summary
                cursor.execute("""
                    INSERT INTO activity_summary (
                        user_id, date, period_type,
                        sedentary_minutes, fidget_minutes, active_minutes, total_minutes,
                        sedentary_percentage, active_percentage,
                        dominant_state, activity_score,
                        alert_count, longest_sedentary_period
                    )
                    VALUES (%s, %s, 'monthly', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, date, period_type)
                    DO UPDATE SET
                        sedentary_minutes = EXCLUDED.sedentary_minutes,
                        fidget_minutes = EXCLUDED.fidget_minutes,
                        active_minutes = EXCLUDED.active_minutes,
                        total_minutes = EXCLUDED.total_minutes,
                        sedentary_percentage = EXCLUDED.sedentary_percentage,
                        active_percentage = EXCLUDED.active_percentage,
                        dominant_state = EXCLUDED.dominant_state,
                        activity_score = EXCLUDED.activity_score,
                        alert_count = EXCLUDED.alert_count,
                        longest_sedentary_period = EXCLUDED.longest_sedentary_period,
                        updated_at = NOW()
                """, (
                    user_id, month_start,
                    stats['sedentary_minutes'], stats['fidget_minutes'],
                    stats['active_minutes'], stats['total_minutes'],
                    stats['sedentary_percentage'], stats['active_percentage'],
                    stats['dominant_state'], stats['activity_score'],
                    stats['alert_count'], stats['longest_sedentary_period']
                ))

                self.conn.commit()
                print(f"Monthly summary saved for month starting {month_start}")

        except Exception as e:
            print(f"Failed to calculate monthly summary: {e}")
            self.conn.rollback()

    def run_nightly_analysis(self):
        """Main entry point for nightly ML analysis"""
        print("=" * 60)
        print("NIGHTLY SEDENTARY BEHAVIOR ANALYSIS")
        print(f"   Timestamp: {datetime.now().isoformat()}")
        print(f"   LOINC Code: {LOINC_CODE} ({LOINC_DISPLAY})")
        print("=" * 60)

        try:
            self.connect()

            # Get all active users (or None for single-user mode)
            users = self.get_active_users()

            if not users:
                print("No users with recent data found")
                # Try running for NULL user (single-user mode)
                users = [None]

            today = datetime.now().date()

            # Process each user
            for user_id in users:
                print(f"\n Processing {'user ' + str(user_id) if user_id else 'default user'}...")

                # Load daily data
                df = self.load_user_data(user_id, hours=24)

                if df.empty:
                    print("   ⏭  Skipping - no data")
                    continue

                # Calculate basic statistics
                stats = self.calculate_basic_stats(df)

                # Perform KMeans clustering
                patterns = self.perform_kmeans_analysis(df)

                # Save daily summary
                self.save_daily_summary(user_id, today, stats, patterns)

                # Calculate weekly summary (if it's Sunday)
                if today.weekday() == 6:  # Sunday
                    week_start = today - timedelta(days=6)
                    print("    Calculating weekly summary...")
                    self.calculate_weekly_summary(user_id, week_start)

                # Calculate monthly summary (if it's last day of month)
                tomorrow = today + timedelta(days=1)
                if tomorrow.day == 1:
                    month_start = today.replace(day=1)
                    print("    Calculating monthly summary...")
                    self.calculate_monthly_summary(user_id, month_start)

            print("\n" + "=" * 60)
            print("NIGHTLY ANALYSIS COMPLETED SUCCESSFULLY")
            print("=" * 60)

        except Exception as e:
            print(f"\n ANALYSIS FAILED: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.close()


def main():
    """Main entry point"""
    analytics = SedentaryAnalytics()
    analytics.run_nightly_analysis()


if __name__ == "__main__":
    main()

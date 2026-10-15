import psycopg2
import pandas as pd
from sklearn.cluster import KMeans
import os

# Environment variables
DB_URL = os.environ.get("DATABASE_URL")
if not DB_URL:
    raise ValueError("DATABASE_URL environment variable must be set")
THRESH_FIDGET = float(os.environ.get("THRESH_FIDGET", "0.020"))
THRESH_ACTIVE = float(os.environ.get("THRESH_ACTIVE", "0.040"))
ML_KMEANS_CLUSTERS = int(os.environ.get("ML_KMEANS_CLUSTERS", "3"))
ML_KMEANS_RANDOM_STATE = int(os.environ.get("ML_KMEANS_RANDOM_STATE", "42"))
ML_KMEANS_N_INIT = int(os.environ.get("ML_KMEANS_N_INIT", "10"))
ML_MIN_SAMPLES_FOR_CLUSTERING = int(os.environ.get("ML_MIN_SAMPLES_FOR_CLUSTERING", "100"))
ML_SAMPLES_PER_MINUTE = int(os.environ.get("ML_SAMPLES_PER_MINUTE", "600"))

def run_analysis():
    print(" Starting Nightly Model Analysis ...")

    # 1. CONNECT TO DATABASE
    try:
        conn = psycopg2.connect(DB_URL)
        
        # Read raw data in the last 24 hours 
        # We fetch the smoothened 'acceleration_val' 
        query = """
            SELECT created_at, acceleration_val, state 
            FROM sedentary_log 
            WHERE created_at > NOW() - INTERVAL '24 HOURS'
            ORDER BY created_at ASC
        """
        df = pd.read_sql(query, conn)
        
        if df.empty:
            print(" No data found for today.")
            return

        print(f"Loaded {len(df)} data points from PostgreSQL.")

        # 2. APPLY THE MODEL LOGIC
        # We can re-verify the classification or calculate stats
        
        # Calculate Logic Stats
        active_count = len(df[df['acceleration_val'] > THRESH_ACTIVE])
        fidget_count = len(df[(df['acceleration_val'] > THRESH_FIDGET) & (df['acceleration_val'] <= THRESH_ACTIVE)])
        sedentary_count = len(df[df['acceleration_val'] <= THRESH_FIDGET])

        # 3. ADVANCED ANALYSIS (KMeans)
        # We use KMeans logic to see if new behaviors emerged today
        if len(df) > ML_MIN_SAMPLES_FOR_CLUSTERING:
            X = df[['acceleration_val']]
            kmeans = KMeans(
                n_clusters=ML_KMEANS_CLUSTERS,
                random_state=ML_KMEANS_RANDOM_STATE,
                n_init=ML_KMEANS_N_INIT
            )
            df['cluster'] = kmeans.fit_predict(X)
            
            # Identify the Deep Sedentary cluster center
            centers = sorted(kmeans.cluster_centers_.flatten())
            print(f"Today's Detected Clusters: {centers}")
            
            # Adaptive Threshold Suggestion
            # If the lowest cluster center shifted, then we need to update thresholds
            suggested_fidget_threshold = (centers[0] + centers[1]) / 2
            print(f"Suggested New Fidget Threshold: {suggested_fidget_threshold:.4f}")

        # 4. SAVE RESULTS (For the History Graph)
        # Convert counts to minutes
        sedentary_mins = round(sedentary_count / ML_SAMPLES_PER_MINUTE, 2)
        active_mins = round((active_count + fidget_count) / ML_SAMPLES_PER_MINUTE, 2)
        
        total_mins = sedentary_mins + active_mins
        score = int((active_mins / total_mins) * 100) if total_mins > 0 else 0
        
        dominant_state = "SEDENTARY"
        if active_mins > sedentary_mins:
            dominant_state = "ACTIVE"

        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO activity_summary (date, sedentary_minutes, active_minutes, dominant_state, activity_score)
            VALUES (CURRENT_DATE, %s, %s, %s, %s)
            ON CONFLICT (date) DO UPDATE 
            SET sedentary_minutes = EXCLUDED.sedentary_minutes,
                active_minutes = EXCLUDED.active_minutes,
                activity_score = EXCLUDED.activity_score;
        """, (sedentary_mins, active_mins, dominant_state, score))
        
        conn.commit()
        print(f"Analysis Saved! Score: {score}/100")
        
    except Exception as e:
        print(f" Error: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    run_analysis()

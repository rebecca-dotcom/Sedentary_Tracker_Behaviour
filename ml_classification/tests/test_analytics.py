"""Unit tests for the activity statistics and KMeans threshold suggestions.

Defaults used by the service: fidget threshold 0.020 g, active threshold
0.040 g, 600 samples per minute (10 Hz).
"""

import numpy as np
import pandas as pd
import pytest

from enhanced_analytics import SedentaryAnalytics

SAMPLES_PER_MINUTE = 600


@pytest.fixture
def analytics():
    return SedentaryAnalytics(db_url="unused")


def readings(acc_values, timers=None):
    data = {"acceleration_val": acc_values}
    if timers is not None:
        data["timer_seconds"] = timers
    return pd.DataFrame(data)


def test_empty_data_gives_neutral_summary(analytics):
    stats = analytics.calculate_basic_stats(pd.DataFrame())
    assert stats["total_minutes"] == 0
    assert stats["activity_score"] == 0
    assert stats["dominant_state"] == "UNKNOWN"


def test_one_minute_of_stillness_is_sedentary(analytics):
    stats = analytics.calculate_basic_stats(readings([0.005] * SAMPLES_PER_MINUTE))
    assert stats["sedentary_minutes"] == 1.0
    assert stats["active_minutes"] == 0
    assert stats["sedentary_percentage"] == 100
    assert stats["dominant_state"] == "SEDENTARY"
    assert stats["activity_score"] == 0


def test_fidgeting_counts_towards_activity(analytics):
    acc = [0.06] * SAMPLES_PER_MINUTE + [0.03] * SAMPLES_PER_MINUTE + [0.005] * SAMPLES_PER_MINUTE
    stats = analytics.calculate_basic_stats(readings(acc))
    assert stats["active_minutes"] == 1.0
    assert stats["fidget_minutes"] == 1.0
    assert stats["sedentary_minutes"] == 1.0
    assert stats["active_percentage"] == pytest.approx(66.67, abs=0.01)
    assert stats["dominant_state"] == "ACTIVE"
    assert stats["activity_score"] == 66


def test_threshold_values_fall_into_lower_state(analytics):
    # Exactly 0.020 is still sedentary; exactly 0.040 is still fidgeting.
    stats = analytics.calculate_basic_stats(readings([0.020] * 600 + [0.040] * 600))
    assert stats["sedentary_minutes"] == 1.0
    assert stats["fidget_minutes"] == 1.0
    assert stats["active_minutes"] == 0


def test_alerts_start_at_twenty_minutes(analytics):
    timers = [0, 600, 1199, 1200, 1500]
    stats = analytics.calculate_basic_stats(readings([0.005] * 5, timers))
    assert stats["alert_count"] == 2
    assert stats["longest_sedentary_period"] == 1500


def test_kmeans_needs_enough_samples(analytics):
    result = analytics.perform_kmeans_analysis(readings([0.01] * 10))
    assert result["patterns"] is None
    assert result["suggested_fidget_threshold"] == 0.020
    assert result["suggested_active_threshold"] == 0.040


def test_kmeans_suggests_thresholds_between_clusters(analytics):
    rng = np.random.default_rng(1)
    acc = np.concatenate([
        rng.normal(0.005, 0.001, 200),  # still
        rng.normal(0.030, 0.002, 200),  # fidgeting
        rng.normal(0.080, 0.005, 200),  # walking
    ])
    result = analytics.perform_kmeans_analysis(readings(acc))
    fidget = result["suggested_fidget_threshold"]
    active = result["suggested_active_threshold"]
    assert 0.005 < fidget < 0.030
    assert 0.030 < active < 0.080
    assert len(result["patterns"]["cluster_centers"]) == 3

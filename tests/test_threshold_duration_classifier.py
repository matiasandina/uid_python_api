import unittest
from datetime import datetime, timedelta

from classifiers.threshold_duration import evaluate


def reading(timestamp: datetime, temperature: float) -> dict:
    return {
        "timestamp": timestamp,
        "temperature": temperature,
        "zone": 1,
        "packet_number": 1,
        "device_name": "Reader-1",
    }


class ThresholdDurationClassifierTests(unittest.TestCase):
    def test_mean_threshold_waits_for_min_samples(self):
        now = datetime(2026, 6, 9, 10, 0, 0)
        result = evaluate(
            "ABC123",
            [
                reading(now - timedelta(seconds=30), 34.0),
                reading(now, 34.0),
            ],
            now,
            {
                "direction": "below",
                "threshold_c": 35.0,
                "required_duration_seconds": 30.0,
                "min_samples": 3,
                "aggregation": "mean",
            },
        )

        self.assertFalse(result["trigger"])
        self.assertFalse(result["meta"]["coverage_ready"])
        self.assertEqual(result["reason"], "waiting for samples")

    def test_mean_threshold_waits_for_required_window_coverage(self):
        now = datetime(2026, 6, 9, 10, 0, 0)
        result = evaluate(
            "ABC123",
            [
                reading(now - timedelta(seconds=10), 34.0),
                reading(now, 34.0),
            ],
            now,
            {
                "direction": "below",
                "threshold_c": 35.0,
                "required_duration_seconds": 300.0,
                "min_samples": 2,
                "aggregation": "mean",
            },
        )

        self.assertFalse(result["trigger"])
        self.assertFalse(result["condition_true"])
        self.assertFalse(result["meta"]["coverage_ready"])
        self.assertTrue(result["meta"]["threshold_met"])
        self.assertEqual(result["reason"], "collecting window")

    def test_mean_threshold_triggers_after_required_window_coverage(self):
        now = datetime(2026, 6, 9, 10, 0, 0)
        result = evaluate(
            "ABC123",
            [
                reading(now - timedelta(seconds=300), 34.0),
                reading(now - timedelta(seconds=150), 34.5),
                reading(now, 34.0),
            ],
            now,
            {
                "direction": "below",
                "threshold_c": 35.0,
                "required_duration_seconds": 300.0,
                "min_samples": 3,
                "aggregation": "mean",
            },
        )

        self.assertTrue(result["trigger"])
        self.assertTrue(result["condition_true"])
        self.assertTrue(result["meta"]["coverage_ready"])
        self.assertEqual(result["reason"], "window ready; mean below threshold")

    def test_default_coverage_tolerance_handles_discrete_sampling_boundary(self):
        now = datetime(2026, 6, 9, 10, 0, 0)
        result = evaluate(
            "ABC123",
            [
                reading(now - timedelta(seconds=29.5), 32.0),
                reading(now - timedelta(seconds=15), 32.5),
                reading(now, 32.0),
            ],
            now,
            {
                "direction": "below",
                "threshold_c": 33.0,
                "required_duration_seconds": 30.0,
                "min_samples": 3,
                "aggregation": "mean",
            },
        )

        self.assertTrue(result["trigger"])
        self.assertTrue(result["meta"]["coverage_ready"])
        self.assertEqual(result["meta"]["coverage_tolerance_seconds"], 1.0)

    def test_coverage_tolerance_does_not_allow_clearly_short_windows(self):
        now = datetime(2026, 6, 9, 10, 0, 0)
        result = evaluate(
            "ABC123",
            [
                reading(now - timedelta(seconds=20), 32.0),
                reading(now - timedelta(seconds=10), 32.5),
                reading(now, 32.0),
            ],
            now,
            {
                "direction": "below",
                "threshold_c": 33.0,
                "required_duration_seconds": 30.0,
                "min_samples": 3,
                "aggregation": "mean",
            },
        )

        self.assertFalse(result["trigger"])
        self.assertFalse(result["meta"]["coverage_ready"])
        self.assertEqual(result["reason"], "collecting window")

    def test_all_threshold_requires_every_observed_sample_to_match(self):
        now = datetime(2026, 6, 9, 10, 0, 0)
        result = evaluate(
            "ABC123",
            [
                reading(now - timedelta(seconds=30), 34.0),
                reading(now - timedelta(seconds=15), 36.0),
                reading(now, 34.0),
            ],
            now,
            {
                "direction": "below",
                "threshold_c": 35.0,
                "required_duration_seconds": 30.0,
                "min_samples": 3,
                "aggregation": "all",
            },
        )

        self.assertFalse(result["trigger"])
        self.assertFalse(result["meta"]["threshold_met"])
        self.assertEqual(result["meta"]["fraction_true"], 2 / 3)
        self.assertEqual(result["reason"], "window ready; all not below threshold")

    def test_below_33_threshold_profile(self):
        now = datetime(2026, 6, 9, 10, 0, 0)
        result = evaluate(
            "ABC123",
            [
                reading(now - timedelta(seconds=30), 32.0),
                reading(now - timedelta(seconds=15), 32.5),
                reading(now, 32.0),
            ],
            now,
            {
                "direction": "below",
                "threshold_c": 33.0,
                "required_duration_seconds": 30.0,
                "min_samples": 3,
                "aggregation": "mean",
            },
        )

        self.assertTrue(result["trigger"])
        self.assertLess(result["meta"]["avg_temp"], 33.0)
        self.assertEqual(result["meta"]["direction"], "below")

    def test_above_direction_is_supported_without_changing_default_profiles(self):
        now = datetime(2026, 6, 9, 10, 0, 0)
        result = evaluate(
            "ABC123",
            [
                reading(now - timedelta(seconds=30), 36.0),
                reading(now - timedelta(seconds=15), 36.5),
                reading(now, 36.0),
            ],
            now,
            {
                "direction": "above",
                "threshold_c": 35.0,
                "required_duration_seconds": 30.0,
                "min_samples": 3,
                "aggregation": "mean",
            },
        )

        self.assertTrue(result["trigger"])
        self.assertEqual(result["meta"]["direction"], "above")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from classifier_temperature_filter import clean_classifier_temperatures


BASE = datetime(2026, 7, 10, 12, 0, 0)


def reading(offset_seconds: float, temperature: float) -> dict:
    return {
        "timestamp": BASE + timedelta(seconds=offset_seconds),
        "temperature": temperature,
        "zone": 1,
        "packet_number": int(offset_seconds * 1000),
        "device_name": "Reader-1",
    }


class ClassifierTemperatureFilterTests(unittest.TestCase):
    def assert_temperatures(self, readings, expected):
        cleaned, _summary = clean_classifier_temperatures(readings)
        self.assertEqual([round(item["temperature"], 3) for item in cleaned], expected)

    def test_probe_min_sentinel_is_replaced_from_past_context(self):
        readings = [
            reading(0, 36.0),
            reading(5, 35.0),
            reading(10, 0.0),
            reading(15, 35.0),
            reading(20, 33.0),
        ]

        cleaned, summary = clean_classifier_temperatures(readings)

        self.assertEqual([item["temperature"] for item in cleaned], [36.0, 35.0, 35.0, 35.0, 33.0])
        self.assertEqual(summary.probe_gate_count, 1)
        self.assertEqual(summary.corrected_count, 1)
        self.assertEqual(summary.dropped_count, 0)
        self.assertEqual(cleaned[2]["temperature_filter"]["reason"], "probe_min_sentinel")

    def test_probe_max_sentinel_is_replaced_from_past_context(self):
        readings = [reading(0, 31.2), reading(1, 50.0), reading(2, 31.2)]

        cleaned, summary = clean_classifier_temperatures(readings)

        self.assertEqual([item["temperature"] for item in cleaned], [31.2, 31.2, 31.2])
        self.assertEqual(summary.probe_gate_count, 1)

    def test_high_non_sentinel_temperature_is_replaced(self):
        readings = [reading(0, 29.4), reading(7, 48.6), reading(47, 29.4)]

        cleaned, summary = clean_classifier_temperatures(readings)

        self.assertEqual([item["temperature"] for item in cleaned], [29.4, 29.4, 29.4])
        self.assertEqual(summary.high_sanity_count, 1)

    def test_fresh_local_jump_is_replaced_without_future_context(self):
        readings = [reading(0, 36.0), reading(1, 40.0), reading(2, 36.0), reading(3, 37.0)]

        cleaned, summary = clean_classifier_temperatures(readings)

        self.assertEqual([item["temperature"] for item in cleaned], [36.0, 36.0, 36.0, 37.0])
        self.assertEqual(summary.local_outlier_count, 1)

    def test_long_gap_change_is_not_flagged_by_stale_context(self):
        readings = [
            reading(0, 35.0),
            reading(5, 34.7),
            reading(10, 34.3),
            reading(600, 27.4),
            reading(610, 26.9),
        ]

        cleaned, summary = clean_classifier_temperatures(readings)

        self.assertEqual([item["temperature"] for item in cleaned], [35.0, 34.7, 34.3, 27.4, 26.9])
        self.assertEqual(summary.corrected_count, 0)

    def test_leading_bad_sample_is_dropped_when_no_past_context_exists(self):
        readings = [reading(0, 50.0), reading(1, 35.0), reading(2, 35.0)]

        cleaned, summary = clean_classifier_temperatures(readings)

        self.assertEqual([item["temperature"] for item in cleaned], [35.0, 35.0])
        self.assertEqual(summary.dropped_count, 1)


if __name__ == "__main__":
    unittest.main()

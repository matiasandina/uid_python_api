import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.plot_temperature_snapshot import (
    Reading,
    _build_payload,
    _build_snapshot_warnings,
    _load_snapshot_windows,
    _load_stim_windows,
)


class PlotTemperatureSnapshotTests(unittest.TestCase):
    def test_recorded_start_without_stop_shades_until_cutoff(self):
        cutoff = datetime(2026, 7, 9, 12, 10, tzinfo=timezone.utc)
        metadata = {
            "triggers_by_animal": {
                "A1": [
                    {
                        "animal_event_id": 1,
                        "animal_id": "A1",
                        "rule_id": "box1",
                        "action": "start",
                        "stimulus_id": "below_35",
                        "reason": "triggered",
                        "meta": {"channels": ["ch1"]},
                        "timestamp": "2026-07-09T12:00:00+00:00",
                    }
                ]
            }
        }

        windows = _load_stim_windows(metadata, ZoneInfo("UTC"), cutoff)

        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].animal_id, "A1")
        self.assertEqual(windows[0].channel_name, "ch1")
        self.assertEqual(windows[0].stop, cutoff)
        self.assertIn("active at cutoff", windows[0].reason)

    def test_inferred_closed_loop_windows_are_added_when_recorded_windows_exist(self):
        cutoff = datetime(2026, 7, 9, 12, 3, tzinfo=timezone.utc)
        readings = [
            Reading(
                timestamp=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
                animal_id="A1",
                temperature_c=34.0,
                source_file="Reader-1.csv",
                zone="1",
            ),
            Reading(
                timestamp=datetime(2026, 7, 9, 12, 1, tzinfo=timezone.utc),
                animal_id="A1",
                temperature_c=34.2,
                source_file="Reader-1.csv",
                zone="1",
            ),
            Reading(
                timestamp=datetime(2026, 7, 9, 12, 2, tzinfo=timezone.utc),
                animal_id="A1",
                temperature_c=36.0,
                source_file="Reader-1.csv",
                zone="1",
            ),
            Reading(
                timestamp=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
                animal_id="B2",
                temperature_c=36.0,
                source_file="Reader-1.csv",
                zone="1",
            ),
        ]
        metadata = {
            "config": {
                "stimulus": {
                    "enabled": True,
                    "mode": "monitor",
                    "control_mode": "closed_loop",
                    "window_on_seconds": 1.0,
                },
                "closed_loop": {
                    "rules": [
                        {
                            "id": "box1",
                            "devices": ["Reader-1"],
                            "classifier": {
                                "plugin": "classifiers.threshold_duration:evaluate",
                                "clf_data_input_window_seconds": 60,
                                "mode": "window",
                                "config": {
                                    "direction": "below",
                                    "threshold_c": 35.0,
                                    "required_duration_seconds": 0.0,
                                    "min_samples": 1,
                                    "stimulus_id": "below_35",
                                },
                            },
                            "outputs": {"laser_channels": ["ch1"]},
                        }
                    ]
                },
            },
            "triggers_by_animal": {
                "B2": [
                    {
                        "animal_event_id": 1,
                        "animal_id": "B2",
                        "rule_id": "box1",
                        "action": "start",
                        "stimulus_id": "manual_check",
                        "reason": "recorded",
                        "meta": {"channels": ["ch1"]},
                        "timestamp": "2026-07-09T12:00:00+00:00",
                    },
                    {
                        "animal_event_id": 2,
                        "animal_id": "B2",
                        "rule_id": "box1",
                        "action": "stop",
                        "stimulus_id": "manual_check",
                        "reason": "recorded stop",
                        "meta": {"channels": ["ch1"]},
                        "timestamp": "2026-07-09T12:00:30+00:00",
                    },
                ]
            },
        }

        windows = _load_snapshot_windows({}, metadata, ZoneInfo("UTC"), readings, cutoff)
        sources_by_animal = {(window.animal_id, window.source) for window in windows}

        self.assertIn(("B2", "recorded"), sources_by_animal)
        self.assertIn(("A1", "inferred"), sources_by_animal)

    def test_missing_metadata_and_config_warns_about_active_session_inference(self):
        warnings = _build_snapshot_warnings(
            metadata_path=None,
            metadata={},
            config_path=None,
            config={},
            windows=[],
        )

        self.assertEqual(len(warnings), 1)
        self.assertIn("active session", warnings[0])
        self.assertIn("--config", warnings[0])

    def test_payload_includes_warning_messages(self):
        reading = Reading(
            timestamp=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
            animal_id="A1",
            temperature_c=36.5,
            source_file="Reader-1.csv",
            zone="1",
        )

        payload = _build_payload([reading], [], 8000, warnings=["Use --config for active sessions."])

        self.assertEqual(payload["warnings"], ["Use --config for active sessions."])


if __name__ == "__main__":
    unittest.main()

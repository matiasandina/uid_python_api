import tempfile
import unittest
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_metadata import write_session_metadata


@dataclass
class DummyEvent:
    animal_event_id: int | None
    animal_id: str
    action: str
    stimulus_id: str
    reason: str
    meta: dict
    timestamp: datetime


class SessionMetadataTests(unittest.TestCase):
    def test_write_session_metadata_groups_events_and_omits_setup_state(self):
        start = datetime(2026, 3, 21, 10, 0, 0)
        end = datetime(2026, 3, 21, 10, 5, 0)
        config = {
            "output_directory": "./data",
            "stimulus": {"enabled": True},
            "_setup_state": {"laser_ready": True, "teensy_ready": True},
        }
        trigger_events = [
            DummyEvent(
                animal_event_id=1,
                animal_id="__open_loop__",
                action="start",
                stimulus_id="__open_loop__",
                reason="open_loop start",
                meta={"channels": ["ch1"], "source": "open_loop"},
                timestamp=start,
            ),
            DummyEvent(
                animal_event_id=2,
                animal_id="__open_loop__",
                action="stop",
                stimulus_id="__open_loop__",
                reason="open_loop stop",
                meta={"channels": ["ch1"], "source": "open_loop"},
                timestamp=end,
            ),
        ]
        missing_events = [
            {"animal_id": "A1", "seconds_since": 42.0, "timestamp": end},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_directory = Path(tmpdir)
            session_folder = output_directory / "2026_03_21_10_00_00_session"
            session_folder.mkdir()

            metadata_path = write_session_metadata(
                output_directory=str(output_directory),
                session_folder=str(session_folder),
                config=config,
                mode="live",
                start_time=start,
                end_time=end,
                trigger_events=trigger_events,
                missing_events=missing_events,
                session_label="bench_check",
            )

            self.assertTrue(metadata_path.exists())
            self.assertEqual(metadata_path, session_folder / "session.yaml")
            self.assertFalse((output_directory / "2026_03_21_10_00_00_session.yaml").exists())
            payload = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["session"]["mode"], "live")
        self.assertEqual(payload["session"]["session_folder"], "2026_03_21_10_00_00_session")
        self.assertEqual(payload["session"]["session_label"], "bench_check")
        self.assertIsNone(payload["session"]["local_timezone"])
        self.assertIsNone(payload["session"]["protocol_name"])
        self.assertEqual(payload["session"]["start_time"], start.isoformat())
        self.assertEqual(payload["session"]["end_time"], end.isoformat())
        self.assertNotIn("_setup_state", payload["config"])
        self.assertIn("__open_loop__", payload["triggers_by_animal"])
        self.assertEqual(len(payload["triggers_by_animal"]["__open_loop__"]), 2)
        self.assertEqual(payload["triggers_by_animal"]["__open_loop__"][0]["meta"]["channels"], ["ch1"])
        self.assertEqual(payload["missing_animals"][0]["animal_id"], "A1")

    def test_write_session_metadata_persists_local_timezone_from_stimulus_start(self):
        start = datetime(2026, 3, 21, 10, 0, 0)
        end = datetime(2026, 3, 21, 10, 5, 0)
        config = {
            "output_directory": "./data",
            "stimulus": {"enabled": True, "start": {"timezone": "America/New_York"}},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            session_folder = Path(tmpdir) / "2026_03_21_10_00_00_session"
            session_folder.mkdir()

            metadata_path = write_session_metadata(
                output_directory=tmpdir,
                session_folder=str(session_folder),
                config=config,
                mode="live",
                start_time=start,
                end_time=end,
                trigger_events=[],
                missing_events=[],
            )
            payload = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["session"]["local_timezone"], "America/New_York")


if __name__ == "__main__":
    unittest.main()

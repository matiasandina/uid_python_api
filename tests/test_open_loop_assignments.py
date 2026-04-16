import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from typed_config import validate_config


class OpenLoopAssignmentConfigTests(unittest.TestCase):
    def test_open_loop_assignments_derive_target_channels(self):
        config = {
            "devices": [
                {"name": "MM23", "host": "example-mm23", "port": 10001},
                {"name": "MM24", "host": "example-mm24", "port": 10001},
            ],
            "output_directory": "./data",
            "stimulus": {
                "enabled": True,
                "mode": "laser",
                "control_mode": "open_loop",
                "dll_path": "C:/Doric/DoricSystem.dll",
                "channels": {
                    "ch3": {"current_ma": 50},
                    "ch4": {"current_ma": 50},
                },
                "open_loop_assignments": [
                    {"id": "MM23", "device": "MM23", "channel": "ch3"},
                    {"id": "MM24", "device": "MM24", "channel": "ch4", "assigned_animal_ids": ["6e69f157"]},
                ],
                "run_for_minutes": 10,
                "start": {"mode": "delay", "delay_seconds": 5},
                "pulse": {"period_ms": 100, "time_on_ms": 10},
            },
            "ttl_capture": {"enabled": True},
        }

        resolved = validate_config(config)
        self.assertEqual(resolved["stimulus"]["target_channels"], ["ch3", "ch4"])
        self.assertEqual(
            resolved["stimulus"]["open_loop_assignments"][1]["assigned_animal_ids"],
            ["6E69F157"],
        )


if __name__ == "__main__":
    unittest.main()

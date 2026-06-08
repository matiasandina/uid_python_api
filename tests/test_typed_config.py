import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import yaml

from device_manager import DeviceManager
from doric_light_source import DEFAULT_DORIC_DLL_DIR
from typed_config import load_config, load_runtime_config, validate_config


MINIMAL_MACHINE_LOCAL = {
    "output_directory": "./data",
    "devices": [
        {"host": "10.0.0.1", "port": 10001, "name": "Reader-1"},
        {"host": "10.0.0.2", "port": 10001, "name": "Reader-2"},
    ],
    "stimulus": {
        "dll_path": "C:/Doric/DoricSystem.dll",
        "channels": {
            "ch1": {"index": 1, "current_ma": 50},
            "ch2": {"index": 2, "current_ma": 50},
            "ch3": {"index": 3, "current_ma": 50},
            "ch4": {"index": 4, "current_ma": 50},
        },
    },
}


class TypedConfigTests(unittest.TestCase):
    def test_runtime_does_not_depend_on_config_example(self):
        config = load_config(local_path="does-not-exist.yaml", require_local=False)
        self.assertEqual(config["output_directory"], "./data")
        self.assertEqual(config["stimulus"]["mode"], "monitor")
        self.assertEqual(config["stimulus"]["control_mode"], None)
        self.assertEqual(config["stimulus"]["dll_path"], str(DEFAULT_DORIC_DLL_DIR))
        self.assertEqual(config["devices"], [])

    def test_laser_config_can_use_default_doric_dll_directory(self):
        config = validate_config(
            {
                "output_directory": "./data",
                "stimulus": {
                    "enabled": True,
                    "mode": "laser",
                    "control_mode": "open_loop",
                    "channels": {"ch1": {"index": 1, "current_ma": 50}},
                    "target_channels": ["ch1"],
                    "run_for_minutes": 1.0,
                    "start": {"mode": "immediate"},
                    "window_on_seconds": 1.0,
                    "pulse": {"period_ms": 1000, "time_on_ms": 10},
                    "train": {"on_seconds": 1.0, "off_seconds": 0.0},
                    "square": {},
                },
                "ttl_capture": {"enabled": True},
            }
        )

        self.assertEqual(config["stimulus"]["dll_path"], str(DEFAULT_DORIC_DLL_DIR))

    def test_overlay_cannot_override_machine_local_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            overlay_path = Path(tmpdir) / "overlay.yaml"
            overlay_path.write_text(
                yaml.safe_dump({"stimulus": {"dll_path": "C:/bad.dll"}}, sort_keys=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "machine-local field 'stimulus.dll_path'"):
                load_config(str(overlay_path), require_local=False)

    def test_open_loop_requires_protocol_fields(self):
        with self.assertRaisesRegex(ValueError, "stimulus.target_channels or stimulus.open_loop_assignments must be non-empty"):
            validate_config(
                {
                    "output_directory": "./data",
                    "stimulus": {
                        "enabled": True,
                        "mode": "laser",
                    "control_mode": "open_loop",
                    "dll_path": "C:/Doric/DoricSystem.dll",
                    "channels": {"ch1": {"index": 1, "current_ma": 50}},
                },
                "ttl_capture": {"enabled": True},
                }
            )

    def test_top_level_yaml_must_be_mapping(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "config.local.yaml"
            local_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "YAML mapping at the top level"):
                load_runtime_config(local_path=str(local_path), require_local=True)

    def test_string_to_list_is_not_coerced_for_target_channels(self):
        with self.assertRaisesRegex(ValueError, "Input should be a valid list"):
            validate_config(
                {
                    "output_directory": "./data",
                    "stimulus": {
                        "enabled": True,
                        "mode": "laser",
                        "control_mode": "open_loop",
                        "dll_path": "C:/Doric/DoricSystem.dll",
                        "channels": {"ch1": {"index": 1, "current_ma": 50}},
                        "target_channels": "ch1",
                        "run_for_minutes": 1.0,
                        "start": {"mode": "immediate"},
                        "window_on_seconds": 1.0,
                        "pulse": {"period_ms": 1000, "time_on_ms": 10},
                        "train": {"on_seconds": 1.0, "off_seconds": 0.0},
                        "square": {},
                    },
                    "ttl_capture": {"enabled": True},
                }
            )

    def test_overlay_reports_machine_local_violation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            overlay_path = Path(tmpdir) / "overlay.yaml"
            overlay_path.write_text(
                yaml.safe_dump({"stimulus": {"port": 7}}, sort_keys=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "machine-local field 'stimulus.port'"):
                load_config(str(overlay_path), local_path="does-not-exist.yaml", require_local=False)

    def test_overlay_cannot_override_doric_uid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            overlay_path = Path(tmpdir) / "overlay.yaml"
            overlay_path.write_text(
                yaml.safe_dump({"stimulus": {"uid": "c006855a20e326fe"}}, sort_keys=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "machine-local field 'stimulus.uid'"):
                load_config(str(overlay_path), local_path="does-not-exist.yaml", require_local=False)

    def test_loader_reports_file_line_and_value_for_bad_channels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "config.local.yaml"
            local_path.write_text(
                yaml.safe_dump({"stimulus": {"channels": {"ch1": 0}}}, sort_keys=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, r"must be between 1 and 8"):
                load_config(local_path=str(local_path), require_local=True)

    def test_overlay_current_ma_merges_with_legacy_numeric_channel_map(self):
        legacy_local = {
            "output_directory": "./data",
            "devices": [{"host": "10.0.0.1", "port": 10001, "name": "Reader-1"}],
            "stimulus": {
                "dll_path": "C:/Doric/DoricSystem.dll",
                "channels": {
                    "ch1": 1,
                    "ch2": 2,
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "config.local.yaml"
            local_path.write_text(yaml.safe_dump(legacy_local, sort_keys=False), encoding="utf-8")

            config = load_runtime_config(
                "configs/open_loop/openloop_1hz_troubleshoot.yaml",
                local_path=str(local_path),
                require_local=True,
            )

            self.assertEqual(config.stimulus.channels["ch1"].index, 0)
            self.assertEqual(config.stimulus.channels["ch1"].current_ma, 50)
            self.assertEqual(config.stimulus.channels["ch2"].index, 1)

    def test_open_loop_accepts_clock_start_schedule(self):
        config = validate_config(
            {
                "output_directory": "./data",
                "stimulus": {
                    "enabled": True,
                    "mode": "laser",
                    "control_mode": "open_loop",
                    "dll_path": "C:/Doric/DoricSystem.dll",
                    "channels": {"ch1": {"index": 1, "current_ma": 50}},
                    "target_channels": ["ch1"],
                    "run_for_minutes": 1.0,
                    "start": {
                        "mode": "clock",
                        "timezone": "America/New_York",
                        "at_hhmm": "13:00",
                        "rollback_next_day": False,
                    },
                    "window_on_seconds": 1.0,
                    "pulse": {"period_ms": 1000, "time_on_ms": 10},
                    "train": {"on_seconds": 1.0, "off_seconds": 0.0},
                    "square": {},
                },
                "ttl_capture": {"enabled": True},
            }
        )

        self.assertEqual(config["stimulus"]["start"]["mode"], "clock")
        self.assertEqual(config["stimulus"]["start"]["at_hhmm"], "13:00")

    def test_closed_loop_rule_config_accepts_device_scoped_rule(self):
        config = validate_config(
            {
                "output_directory": "./data",
                "devices": [{"host": "10.0.0.1", "port": 10001, "name": "Reader-1"}],
                "closed_loop": {
                    "rules": [
                        {
                            "id": "box1",
                            "devices": ["Reader-1"],
                            "classifier": {
                                "plugin": "classifiers.example:evaluate",
                                "evaluate_interval_seconds": 0.5,
                                "clf_data_input_window_seconds": 60.0,
                                "missing_animal_seconds": 120.0,
                                "mode": "window",
                                "config": {"threshold_c": 35.0},
                            },
                            "outputs": {"laser_channels": ["ch1"]},
                        }
                    ]
                },
                "stimulus": {
                    "enabled": True,
                    "mode": "laser",
                    "control_mode": "closed_loop",
                    "dll_path": "C:/Doric/DoricSystem.dll",
                    "channels": {"ch1": {"index": 1, "current_ma": 50}},
                    "window_on_seconds": 1.0,
                    "pulse": {"period_ms": 1000, "time_on_ms": 10},
                    "train": {"on_seconds": 1.0, "off_seconds": 0.0},
                    "square": {},
                },
                "ttl_capture": {"enabled": True},
            }
        )

        self.assertEqual(config["closed_loop"]["rules"][0]["id"], "box1")
        self.assertEqual(config["closed_loop"]["rules"][0]["outputs"]["laser_channels"], ["ch1"])

    def test_runtime_translates_legacy_start_delay_into_start_block(self):
        config = validate_config(
            {
                "output_directory": "./data",
                "stimulus": {
                    "enabled": True,
                    "mode": "laser",
                    "control_mode": "open_loop",
                    "dll_path": "C:/Doric/DoricSystem.dll",
                    "channels": {"ch1": {"index": 1, "current_ma": 50}},
                    "target_channels": ["ch1"],
                    "run_for_minutes": 1.0,
                    "start_delay_seconds": 30.0,
                    "window_on_seconds": 1.0,
                    "pulse": {"period_ms": 1000, "time_on_ms": 10},
                    "train": {"on_seconds": 1.0, "off_seconds": 0.0},
                    "square": {},
                },
                "ttl_capture": {"enabled": True},
            }
        )

        self.assertEqual(config["stimulus"]["start"]["mode"], "delay")
        self.assertEqual(config["stimulus"]["start"]["delay_seconds"], 30.0)

    def test_start_block_and_legacy_delay_cannot_both_be_set(self):
        with self.assertRaisesRegex(ValueError, "Use either legacy stimulus.start_delay_seconds or stimulus.start"):
            validate_config(
                {
                    "output_directory": "./data",
                    "stimulus": {
                        "enabled": True,
                        "mode": "laser",
                        "control_mode": "open_loop",
                        "dll_path": "C:/Doric/DoricSystem.dll",
                        "channels": {"ch1": {"index": 1, "current_ma": 50}},
                        "target_channels": ["ch1"],
                        "run_for_minutes": 1.0,
                        "start_delay_seconds": 30.0,
                        "start": {"mode": "delay", "delay_seconds": 15.0},
                        "window_on_seconds": 1.0,
                        "pulse": {"period_ms": 1000, "time_on_ms": 10},
                        "train": {"on_seconds": 1.0, "off_seconds": 0.0},
                        "square": {},
                    },
                    "ttl_capture": {"enabled": True},
                }
            )

    def test_open_loop_clock_start_rolls_to_next_day_when_enabled(self):
        manager = DeviceManager(
            stimulus_config={
                "enabled": True,
                "control_mode": "open_loop",
                "target_channels": ["ch1"],
                "run_for_minutes": 1.0,
                "start": {
                    "mode": "clock",
                    "timezone": "America/New_York",
                    "at_hhmm": "08:00",
                    "rollback_next_day": True,
                },
            }
        )

        launch_time = datetime.fromisoformat("2026-04-11T15:00:00-04:00")
        delay_seconds, scheduled_at = manager._resolve_open_loop_start(launch_time)

        self.assertIsNotNone(scheduled_at)
        self.assertEqual(scheduled_at.isoformat(), "2026-04-12T08:00:00-04:00")
        self.assertEqual(delay_seconds, 17 * 3600)

    def test_open_loop_clock_start_rejects_past_time_without_rollover(self):
        manager = DeviceManager(
            stimulus_config={
                "enabled": True,
                "control_mode": "open_loop",
                "target_channels": ["ch1"],
                "run_for_minutes": 1.0,
                "start": {
                    "mode": "clock",
                    "timezone": "America/New_York",
                    "at_hhmm": "08:00",
                    "rollback_next_day": False,
                },
            }
        )

        launch_time = datetime.fromisoformat("2026-04-11T15:00:00-04:00")
        with self.assertRaisesRegex(ValueError, "earlier than launch time"):
            manager._resolve_open_loop_start(launch_time)

    def test_open_loop_clock_start_treats_naive_launch_time_as_local_time(self):
        manager = DeviceManager(
            stimulus_config={
                "enabled": True,
                "control_mode": "open_loop",
                "target_channels": ["ch1"],
                "run_for_minutes": 1.0,
                "start": {
                    "mode": "clock",
                    "timezone": "America/New_York",
                    "at_hhmm": "13:00",
                    "rollback_next_day": False,
                },
            }
        )

        launch_time = datetime(2026, 4, 11, 9, 45, 0)
        delay_seconds, scheduled_at = manager._resolve_open_loop_start(launch_time)

        self.assertIsNotNone(scheduled_at)
        self.assertEqual(scheduled_at.isoformat(), "2026-04-11T13:00:00-04:00")
        self.assertEqual(delay_seconds, 3.25 * 3600)

    def test_repo_overlays_load_with_minimal_machine_local_config(self):
        overlay_paths = [
            Path("configs/open_loop/openloop_1hz_troubleshoot.yaml"),
            Path("configs/open_loop/openloop_5hz.yaml"),
            Path("configs/open_loop/openloop_10hz.yaml"),
            Path("configs/open_loop/openloop_20hz.yaml"),
            Path("configs/closed_loop/stim_below35.yaml"),
            Path("configs/closed_loop/closedloop_monitor_below33_30s.yaml"),
            Path("configs/closed_loop/closedloop_laser_below33_30s.yaml"),
            Path("configs/closed_loop/closedloop_laser_below35_5min.yaml"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "config.local.yaml"
            local_path.write_text(yaml.safe_dump(MINIMAL_MACHINE_LOCAL, sort_keys=False), encoding="utf-8")
            for overlay_path in overlay_paths:
                with self.subTest(overlay=str(overlay_path)):
                    config = load_config(str(overlay_path), local_path=str(local_path), require_local=True)
                    self.assertIsInstance(config, dict)


if __name__ == "__main__":
    unittest.main()

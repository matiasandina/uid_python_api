import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis_tools import load_analysis_session
from analysis_tools.triggers import build_stimulation_windows
from analysis_tools.ttl import (
    TTL_PULSE_SCHEMA,
    build_ttl_qc_table,
    reconstruct_sample_masks_from_pulses,
)
from ttl_capture.frame_index import TTLFrameIndexWriter


class AnalysisToolsTests(unittest.TestCase):
    def test_closed_loop_stimulation_windows_derive_assignment_from_animal_event(self):
        trigger_events = pl.from_dicts(
            [
                {
                    "session_name": "session",
                    "animal_id": "6e69f157",
                    "animal_event_id": 1,
                    "rule_id": "below35",
                    "action": "start",
                    "stimulus_id": "laser",
                    "reason": "triggered",
                    "channels": ["ch4"],
                    "meta_json": json.dumps({"devices": ["MM24"], "channels": ["ch4"]}),
                    "timestamp_local": datetime(2026, 4, 15, 12, 0, 0),
                    "timestamp_utc": datetime(2026, 4, 15, 16, 0, 0, tzinfo=timezone.utc),
                },
                {
                    "session_name": "session",
                    "animal_id": "6e69f157",
                    "animal_event_id": 2,
                    "rule_id": "below35",
                    "action": "stop",
                    "stimulus_id": "laser",
                    "reason": "recovered",
                    "channels": ["ch4"],
                    "meta_json": json.dumps({"devices": ["MM24"], "channels": ["ch4"]}),
                    "timestamp_local": datetime(2026, 4, 15, 12, 1, 0),
                    "timestamp_utc": datetime(2026, 4, 15, 16, 1, 0, tzinfo=timezone.utc),
                },
            ]
        )

        windows = build_stimulation_windows(trigger_events)

        self.assertEqual(windows.height, 1)
        row = windows.row(0, named=True)
        self.assertEqual(row["assignment_id"], "MM24")
        self.assertEqual(row["assigned_animal_ids"], ["6E69F157"])

    def test_open_loop_stimulation_windows_without_assignments_keep_no_assignment(self):
        trigger_events = pl.from_dicts(
            [
                {
                    "session_name": "session",
                    "animal_id": "__open_loop__",
                    "animal_event_id": 1,
                    "rule_id": "__open_loop__",
                    "action": "start",
                    "stimulus_id": "__open_loop__",
                    "reason": "start",
                    "channels": ["ch1"],
                    "meta_json": json.dumps({"channels": ["ch1"]}),
                    "timestamp_local": datetime(2026, 4, 15, 12, 0, 0),
                    "timestamp_utc": datetime(2026, 4, 15, 16, 0, 0, tzinfo=timezone.utc),
                },
                {
                    "session_name": "session",
                    "animal_id": "__open_loop__",
                    "animal_event_id": 2,
                    "rule_id": "__open_loop__",
                    "action": "stop",
                    "stimulus_id": "__open_loop__",
                    "reason": "stop",
                    "channels": ["ch1"],
                    "meta_json": json.dumps({"channels": ["ch1"]}),
                    "timestamp_local": datetime(2026, 4, 15, 12, 1, 0),
                    "timestamp_utc": datetime(2026, 4, 15, 16, 1, 0, tzinfo=timezone.utc),
                },
            ]
        )

        windows = build_stimulation_windows(trigger_events)

        self.assertEqual(windows.height, 1)
        row = windows.row(0, named=True)
        self.assertIsNone(row["assignment_id"])
        self.assertEqual(row["assigned_animal_ids"], [])

    def test_ttl_qc_uses_within_bout_pulse_frequency_for_train_stimulation(self):
        pulse_rows = []
        pulse_width_ms = 10.0
        pulse_width_ns = int(pulse_width_ms * 1_000_000)
        for bout_idx in range(5):
            bout_start_ns = bout_idx * 4_000_000_000
            for pulse_idx in range(10):
                start_ns = bout_start_ns + (pulse_idx * 100_000_000)
                pulse_rows.append(
                    {
                        "session_name": "session",
                        "channel_name": "ch1",
                        "start_sample_index": 0,
                        "stop_sample_index": 0,
                        "start_timestamp_monotonic_ns": start_ns,
                        "stop_timestamp_monotonic_ns": start_ns + pulse_width_ns,
                        "start_timestamp_estimated_utc": None,
                        "stop_timestamp_estimated_utc": None,
                        "pulse_width_ms": pulse_width_ms,
                    }
                )

        ttl_pulses = pl.from_dicts(pulse_rows, schema=TTL_PULSE_SCHEMA)
        ttl_qc = build_ttl_qc_table(
            pl.DataFrame(schema={"channel_name": pl.Utf8, "edge_type": pl.Utf8}),
            ttl_pulses,
            stimulus_config={
                "channels": {"ch1": {"index": 0}},
                "target_channels": ["ch1"],
                "pulse": {"period_ms": 100.0, "time_on_ms": 10.0},
            },
        )

        row = ttl_qc.row(0, named=True)
        self.assertAlmostEqual(row["observed_frequency_hz"], 10.0, places=6)
        self.assertAlmostEqual(row["observed_effective_frequency_hz"], 49.0 / 16.9, places=6)
        self.assertTrue(row["frequency_ok"])
        self.assertEqual(row["note"], "active channel within tolerance")

    def test_load_analysis_session_normalizes_time_and_stim_windows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026_04_15_12_00_00_test"
            session_dir.mkdir()

            metadata = {
                "session": {
                    "session_folder": session_dir.name,
                    "session_label": "test",
                    "protocol_name": "openloop_20hz",
                    "mode": "live",
                    "start_time": "2026-04-15T12:00:00",
                    "end_time": "2026-04-15T12:10:00",
                },
                "config": {
                    "stimulus": {
                        "enabled": True,
                        "start": {"timezone": "America/New_York"},
                        "channels": {"ch1": {"index": 0, "current_ma": 50.0}},
                        "target_channels": ["ch1"],
                        "pulse": {"period_ms": 100.0, "time_on_ms": 10.0},
                    },
                    "ttl_capture": {"enabled": True},
                },
                "triggers_by_animal": {
                    "__open_loop__": [
                        {
                            "animal_event_id": 1,
                            "animal_id": "__open_loop__",
                            "rule_id": "__open_loop__",
                            "action": "start",
                            "stimulus_id": "__open_loop__",
                            "reason": "open_loop start",
                            "meta": {"channels": ["ch1"]},
                            "timestamp": "2026-04-15T12:00:30",
                        },
                        {
                            "animal_event_id": 2,
                            "animal_id": "__open_loop__",
                            "rule_id": "__open_loop__",
                            "action": "stop",
                            "stimulus_id": "__open_loop__",
                            "reason": "open_loop stop",
                            "meta": {"channels": ["ch1"]},
                            "timestamp": "2026-04-15T12:01:00",
                        },
                    ]
                },
            }
            (session_dir / "session.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
            (session_dir / "Reader-1.csv").write_text(
                "DateTime,UID,Temperature,Zone\n"
                "2026-04-15 12:00:20.000,A1,36.1,1\n"
                "2026-04-15 12:00:20.000,A1,36.2,1\n"
                "2026-04-15 12:00:20.000,B2,34.2,2\n"
                "2026-04-15 12:00:40.000,A1,36.3,1\n",
                encoding="utf-8",
            )
            (session_dir / "ttl_meta.json").write_text(
                json.dumps(
                    {
                        "sampling_rate_hz": 20000,
                        "frame_size": 4,
                        "channel_map": [1, 2, 3, 4],
                        "t0_monotonic_ns": 1_000_000_000,
                        "t0_frame_id": 0,
                        "wall_clock_start_iso": "2026-04-15T16:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (session_dir / "ttl_raw.bin").write_bytes(bytes([0, 1, 0, 0]))

            result = load_analysis_session(session_dir, ttl_active_low=False)

            self.assertEqual(result.local_timezone, "America/New_York")
            self.assertEqual(result.temperature.height, 3)
            self.assertEqual(result.temperature["animal_id"].to_list(), ["A1", "B2", "A1"])
            self.assertEqual(result.stimulation_windows.height, 1)
            annotated = result.temperature_annotated.sort("timestamp_utc")
            self.assertEqual(annotated["is_stim_active"].to_list(), [False, False, True])
            self.assertEqual(result.ttl_edges.height, 2)
            self.assertEqual(result.ttl_pulses.height, 1)
            self.assertEqual(result.ttl_qc.height, 1)
            qc = {row["channel_name"]: row for row in result.ttl_qc.to_dicts()}
            self.assertTrue(qc["ch1"]["expected_active"])
            self.assertEqual(qc["ch1"]["pulse_count"], 1)
            reconstructed = reconstruct_sample_masks_from_pulses(result.ttl_pulses, total_samples=4)
            self.assertEqual(reconstructed, bytes([0, 1, 0, 0]))

    def test_load_analysis_session_prefers_frame_index_when_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "2026_04_15_12_00_00_indexed"
            session_dir.mkdir()

            metadata = {
                "session": {
                    "session_folder": session_dir.name,
                    "session_label": "indexed",
                    "protocol_name": "openloop_20hz",
                    "mode": "live",
                    "start_time": "2026-04-15T12:00:00",
                    "end_time": "2026-04-15T12:10:00",
                },
                "config": {
                    "stimulus": {
                        "enabled": True,
                        "start": {"timezone": "America/New_York"},
                        "channels": {"ch1": {"index": 0, "current_ma": 50.0}},
                        "target_channels": ["ch1"],
                        "pulse": {"period_ms": 100.0, "time_on_ms": 10.0},
                    },
                    "ttl_capture": {"enabled": True},
                },
                "triggers_by_animal": {},
            }
            (session_dir / "session.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
            (session_dir / "Reader-1.csv").write_text(
                "DateTime,UID,Temperature,Zone\n"
                "2026-04-15 12:00:20.000,A1,36.1,1\n",
                encoding="utf-8",
            )
            (session_dir / "ttl_meta.json").write_text(
                json.dumps(
                    {
                        "sampling_rate_hz": 1000,
                        "frame_size": 4,
                        "channel_map": [1, 2, 3, 4],
                        "t0_monotonic_ns": 1_000_000_000,
                        "t0_frame_id": 0,
                        "wall_clock_start_iso": "2026-04-15T16:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (session_dir / "ttl_raw.bin").write_bytes(bytes([
                0, 1, 1, 0,
                0, 1, 1, 0,
            ]))
            with TTLFrameIndexWriter(session_dir / "ttl_frames.bin", sampling_rate_hz=1000, frame_size=4) as writer:
                writer.append(frame_id=0, t_us_first_sample=0, payload_offset_bytes=0)
                writer.append(frame_id=2, t_us_first_sample=8000, payload_offset_bytes=4)

            result = load_analysis_session(session_dir, ttl_active_low=False)

        self.assertEqual(result.ttl_edges.height, 4)
        rising_samples = (
            result.ttl_edges
            .filter(pl.col("channel_name") == "ch1")
            .filter(pl.col("edge_type") == "rising")
            .sort("sample_index")
            .get_column("sample_index")
            .to_list()
        )
        self.assertEqual(rising_samples, [1, 9])
        pulse_starts = (
            result.ttl_pulses
            .sort("start_sample_index")
            .get_column("start_sample_index")
            .to_list()
        )
        self.assertEqual(pulse_starts, [1, 9])


if __name__ == "__main__":
    unittest.main()

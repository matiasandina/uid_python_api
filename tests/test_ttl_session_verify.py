import json
import tempfile
import unittest
from pathlib import Path

import yaml

from ttl_capture.frame_index import TTLFrameIndexWriter
from ttl_capture.session_verify import verify_session


class TTLSessionVerifyTests(unittest.TestCase):
    def test_verify_session_matches_ttl_edges_to_command_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            session_dir = root / "2026_03_21_10_00_00_session"
            session_dir.mkdir()
            metadata_path = session_dir / "session.yaml"

            ttl_meta = {
                "sampling_rate_hz": 1000,
                "frame_size": 10,
                "channel_map": [1, 2, 3, 4],
                "t0_monotonic_ns": 0,
                "t0_frame_id": 0,
            }
            (session_dir / "ttl_meta.json").write_text(json.dumps(ttl_meta), encoding="utf-8")

            # Rising edges on ch1 at samples 10 and 20, both inside the commanded window.
            payload = bytes([
                0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                1, 1, 0, 0, 0, 0, 0, 0, 0, 0,
                1, 1, 0, 0, 0, 0, 0, 0, 0, 0,
            ])
            (session_dir / "ttl_raw.bin").write_bytes(payload)

            metadata = {
                "triggers_by_animal": {
                    "__open_loop__": [
                        {
                            "action": "start",
                            "reason": "open_loop start",
                            "timestamp": "2026-03-21T10:00:00",
                            "meta": {"channels": ["ch1"], "recorded_monotonic_ns": 5_000_000},
                        },
                        {
                            "action": "stop",
                            "reason": "open_loop stop",
                            "timestamp": "2026-03-21T10:00:01",
                            "meta": {"channels": ["ch1"], "recorded_monotonic_ns": 25_000_000},
                        },
                    ]
                }
            }
            metadata_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

            report = verify_session(metadata_path, tolerance_ms=0.0)

        self.assertTrue(report.ttl_enabled)
        self.assertEqual(report.windows_verified, 1)
        self.assertEqual(report.windows_ok, 1)
        self.assertEqual(report.stray_rising_edges, 0)
        self.assertEqual(report.channel_summaries["ch1"]["rising_edges"], 2)
        self.assertEqual(report.window_results[0].pulse_count, 2)
        self.assertEqual(report.window_results[0].inferred_frequency_hz, 100.0)

    def test_verify_session_reports_stray_edges_outside_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            session_dir = root / "2026_03_21_10_00_00_session"
            session_dir.mkdir()
            metadata_path = session_dir / "session.yaml"

            ttl_meta = {
                "sampling_rate_hz": 1000,
                "frame_size": 10,
                "channel_map": [1, 2, 3, 4],
                "t0_monotonic_ns": 0,
                "t0_frame_id": 0,
            }
            (session_dir / "ttl_meta.json").write_text(json.dumps(ttl_meta), encoding="utf-8")

            # One rising edge at sample 2 before the command window, one at sample 12 inside it.
            payload = bytes([
                0, 0, 1, 1, 0, 0, 0, 0, 0, 0,
                0, 0, 1, 1, 0, 0, 0, 0, 0, 0,
            ])
            (session_dir / "ttl_raw.bin").write_bytes(payload)

            metadata = {
                "triggers_by_animal": {
                    "__open_loop__": [
                        {
                            "action": "start",
                            "reason": "open_loop start",
                            "timestamp": "2026-03-21T10:00:00",
                            "meta": {"channels": ["ch1"], "recorded_monotonic_ns": 10_000_000},
                        },
                        {
                            "action": "stop",
                            "reason": "open_loop stop",
                            "timestamp": "2026-03-21T10:00:01",
                            "meta": {"channels": ["ch1"], "recorded_monotonic_ns": 15_000_000},
                        },
                    ]
                }
            }
            metadata_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

            report = verify_session(metadata_path, tolerance_ms=0.0)

        self.assertEqual(report.windows_verified, 1)
        self.assertEqual(report.windows_ok, 1)
        self.assertEqual(report.stray_rising_edges, 1)
        self.assertTrue(any("outside commanded windows" in issue for issue in report.issues))

    def test_verify_session_uses_frame_index_when_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            session_dir = root / "2026_03_21_10_00_00_session"
            session_dir.mkdir()
            metadata_path = session_dir / "session.yaml"

            ttl_meta = {
                "sampling_rate_hz": 1000,
                "frame_size": 4,
                "channel_map": [1, 2, 3, 4],
                "t0_monotonic_ns": 0,
                "t0_frame_id": 0,
            }
            (session_dir / "ttl_meta.json").write_text(json.dumps(ttl_meta), encoding="utf-8")
            (session_dir / "ttl_raw.bin").write_bytes(bytes([
                0, 1, 1, 0,
                0, 1, 1, 0,
            ]))
            with TTLFrameIndexWriter(session_dir / "ttl_frames.bin", sampling_rate_hz=1000, frame_size=4) as writer:
                writer.append(frame_id=0, t_us_first_sample=0, payload_offset_bytes=0)
                writer.append(frame_id=2, t_us_first_sample=8000, payload_offset_bytes=4)

            metadata = {
                "triggers_by_animal": {
                    "__open_loop__": [
                        {
                            "action": "start",
                            "reason": "open_loop start",
                            "timestamp": "2026-03-21T10:00:00",
                            "meta": {"channels": ["ch1"], "recorded_monotonic_ns": 0},
                        },
                        {
                            "action": "stop",
                            "reason": "open_loop stop",
                            "timestamp": "2026-03-21T10:00:01",
                            "meta": {"channels": ["ch1"], "recorded_monotonic_ns": 12_000_000},
                        },
                    ]
                }
            }
            metadata_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

            report = verify_session(metadata_path, tolerance_ms=0.0)

        self.assertEqual(report.windows_verified, 1)
        self.assertEqual(report.window_results[0].pulse_count, 2)
        self.assertEqual(report.window_results[0].inferred_frequency_hz, 125.0)


if __name__ == "__main__":
    unittest.main()

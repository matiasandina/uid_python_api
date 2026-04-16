import unittest

from preflight_ui import (
    _build_preflight_menu_options,
    build_doric_probe_candidates,
    split_doric_probe_candidates,
    build_square_wave_preview,
    compute_duty_cycle,
    parse_bool_text,
    parse_float_text,
    parse_int_text,
    summarize_windows_usb_matches,
    summarize_channels,
    summarize_channel_list,
)
from live_state import LiveState


class PreflightUiPureFunctionTests(unittest.TestCase):
    def test_parse_bool_text(self):
        self.assertTrue(parse_bool_text("yes"))
        self.assertFalse(parse_bool_text("OFF"))
        with self.assertRaises(ValueError):
            parse_bool_text("maybe")

    def test_parse_numbers(self):
        self.assertEqual(parse_int_text("5"), 5)
        self.assertAlmostEqual(parse_float_text("2.5"), 2.5)
        with self.assertRaises(ValueError):
            parse_int_text("3.2")

    def test_derived_summaries(self):
        duty = compute_duty_cycle({"period_ms": 100, "time_on_ms": 25})
        self.assertAlmostEqual(duty, 0.25)
        self.assertEqual(summarize_channels({"blue": 0}), "blue")
        self.assertEqual(summarize_channel_list(["ch1", "ch2"]), "ch1, ch2")
        wave = build_square_wave_preview(period_ms=100, time_on_ms=25, width=20)
        self.assertIn("on=25.0ms", wave)
        self.assertIn("freq=10.00Hz", wave)

    def test_build_doric_probe_candidates_prefers_explicit_and_dedupes(self):
        candidates = build_doric_probe_candidates(
            {
                "port": 194,
                "discovery": {
                    "candidate_ports": [194, 200, 5],
                    "probe_min_port": 1,
                    "probe_max_port": 3,
                },
            }
        )
        self.assertEqual(candidates, [194, 200, 5, 1, 2, 3])

    def test_split_doric_probe_candidates_separates_preferred_from_fallback(self):
        preferred, fallback = split_doric_probe_candidates(
            {
                "port": 194,
                "discovery": {
                    "candidate_ports": [194, 200, 5],
                    "probe_min_port": 1,
                    "probe_max_port": 3,
                },
            }
        )
        self.assertEqual(preferred, [194, 200, 5])
        self.assertEqual(fallback, [1, 2, 3])

    def test_summarize_windows_usb_matches_empty(self):
        self.assertEqual(
            summarize_windows_usb_matches([]),
            ["No matching Windows USB devices found for the configured filter."],
        )

    def test_bench_menu_option_enabled_with_uid_when_teensy_not_required(self):
        options = _build_preflight_menu_options(
            {
                "stimulus": {
                    "uid": "3262e860053bc222",
                    "port": None,
                    "mode": "monitor",
                },
                "ttl_capture": {"enabled": False},
            },
            LiveState(),
        )
        self.assertEqual(options[2], "Test Laser Program w/o Animals")


if __name__ == "__main__":
    unittest.main()

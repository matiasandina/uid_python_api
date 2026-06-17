import unittest

from console_display import ConsoleDisplay


class ConsoleDisplayTests(unittest.TestCase):
    def test_pulse_timing_handles_unconfigured_square(self):
        self.assertEqual(
            ConsoleDisplay._format_pulse_timing({"period_ms": None, "time_on_ms": None}),
            "n/a",
        )

    def test_pulse_timing_formats_configured_square(self):
        self.assertEqual(
            ConsoleDisplay._format_pulse_timing({"period_ms": 50, "time_on_ms": 10}),
            "10 ms ON / 40 ms OFF",
        )


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime

from session_naming import build_session_folder_name, sanitize_session_label


class SessionNamingTests(unittest.TestCase):
    def test_blank_label_uses_timestamp_only(self):
        started_at = datetime(2026, 4, 9, 11, 24, 3)
        self.assertEqual(build_session_folder_name(started_at, ""), "2026_04_09_11_24_03")
        self.assertEqual(build_session_folder_name(started_at, None), "2026_04_09_11_24_03")

    def test_label_is_sanitized_for_folder_name(self):
        started_at = datetime(2026, 4, 9, 11, 24, 3)
        self.assertEqual(
            build_session_folder_name(started_at, "agrp fasted / cohort 2"),
            "2026_04_09_11_24_03_agrp_fasted_cohort_2",
        )

    def test_sanitize_session_label_collapses_invalid_runs(self):
        self.assertEqual(sanitize_session_label("  !!! pilot__run ??? "), "pilot_run")


if __name__ == "__main__":
    unittest.main()

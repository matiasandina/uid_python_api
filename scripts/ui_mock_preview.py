#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rich.console import Console

from ui_renderers import (
    MenuOption,
    StatusItem,
    build_launch_summary,
    build_preflight_dashboard,
    build_runtime_dashboard,
)


def _preflight_screen() -> object:
    return build_preflight_dashboard(
        menu_options=[
            MenuOption("Find Doric Device"),
            MenuOption("Setup Teensy"),
            MenuOption("Test Laser Program w/o Animals", enabled=False),
            MenuOption("Launch Experiment"),
            MenuOption("Show Config"),
            MenuOption("Exit"),
        ],
        selected_index=3,
        status_items=[
            StatusItem("Find Doric Device", "OK", "COM4 selected"),
            StatusItem("Setup Teensy", "OK", "COM7 handshake verified"),
            StatusItem("Bench Test", "MISSING", "not run yet"),
            StatusItem("Launch Readiness", "READY", "hardware checks complete"),
        ],
        summary_sections=[
            {
                "title": "Experiment",
                "rows": [
                    ("Run Mode", "Closed Loop", "bold cyan"),
                    ("Output", "Real laser output possible", "bold yellow"),
                    ("Target Channels", "left:1, right:2", "bold white"),
                    ("Data Saved To", "data/2026-04-03_mouse-cohort-a", "bold green"),
                ],
            },
            {
                "title": "Stimulation",
                "rows": [
                    ("Pulse Frequency", "20 Hz", "bold white"),
                    ("Pulse Timing", "10 ms ON / 40 ms OFF", "bold white"),
                    ("Train Timing", "1.0 s ON / 3.0 s OFF", "bold white"),
                    ("Laser Current", "55 mA", "bold white"),
                ],
            },
            {
                "title": "Rules",
                "rows": [
                    ("Total Duration", "Manual exit", "bold white"),
                    ("Classifier", "classifiers.example:evaluate", "bold white"),
                    ("Eval Window", "10 s", "bold white"),
                    ("TTL Capture", "Enabled on COM7", "bold white"),
                ],
            },
        ],
    )


def _launch_screen() -> object:
    return build_launch_summary(
        readiness_items=[
            StatusItem("Hardware Verification", "OK", "Doric device selected and armed"),
            StatusItem("TTL Capture", "OK", "Teensy handshake verified"),
            StatusItem("Output Behavior", "ACTIVE", "real laser output can occur"),
        ],
        summary_sections=[
            {
                "title": "Launch Contract",
                "rows": [
                    ("Run Mode", "Open Loop", "bold cyan"),
                    ("Resolved Channels", "left:1, right:2", "bold white"),
                    ("Pulse Frequency", "20 Hz", "bold white"),
                    ("Pulse Timing", "10 ms ON / 40 ms OFF", "bold white"),
                    ("Train Timing", "1.0 s ON / 3.0 s OFF", "bold white"),
                    ("Laser Current", "55 mA", "bold white"),
                    ("Start Delay", "00:30", "bold yellow"),
                    ("Total Duration", "Manual exit", "bold white"),
                    ("Expected Stim Time", "15 min", "bold white"),
                ],
            }
        ],
        note="Start delay begins after you confirm launch. This is the moment to finish setting up your animals if you haven't.",
    )


def _runtime_screen(state: str) -> object:
    if state == "active":
        state_label = "Laser Active"
        laser_label = "ACTIVE ON ch1,ch2"
        session_time = "00:06 / 00:15"
    elif state == "done":
        state_label = "Completed"
        laser_label = "OFF"
        session_time = "00:15 / 00:15"
    else:
        state_label = "Waiting for start delay"
        laser_label = "OFF"
        session_time = "00:00 / 00:15"

    return build_runtime_dashboard(
        output_directory="data/2026-04-03_mouse-cohort-a/session_001",
        device_summary=[
            ("Devices Configured", "2"),
            ("Animals Tracked", "6"),
            ("Total Readings", "8,421"),
            ("Parse Success", "99.8%"),
        ],
        devices=[
            ("Reader-1", "10.0.127.107:10001", "OK", "4210", "2026-04-03 03:58:01pm"),
            ("Reader-2", "10.0.127.108:10001", "OK", "4211", "2026-04-03 03:58:02pm"),
        ],
        animals=[
            ("ABCD1234", "36.8", "2026-04-03 03:58:01pm", "1", "1520", "OK"),
            ("EFGH5678", "37.1", "2026-04-03 03:58:00pm", "2", "1495", "OK"),
            ("IJKL9012", "36.4", "2026-04-03 03:57:56pm", "1", "1390", "OK"),
            ("MNOP3456", "35.9", "2026-04-03 03:57:44pm", "2", "1301", "MISSING"),
        ],
        state_label=state_label,
        laser_label=laser_label,
        session_time=session_time,
        save_path="data/2026-04-03_mouse-cohort-a/session_001",
        stim_rows=[
            ("Run Mode", "Open Loop"),
            ("Target Channels", "left:1, right:2"),
            ("Pulse Frequency", "20 Hz"),
            ("Pulse Timing", "10 ms ON / 40 ms OFF"),
            ("Train Timing", "1.0 s ON / 3.0 s OFF"),
            ("Laser Current", "55 mA"),
            ("Expected Stim Time", "15 min"),
        ],
        event_rows=[
            ("Latest Stim Event", "Pulse train started"),
            ("TTL Capture", "edges detected on 2 channels"),
            ("Program State", state_label),
            ("Last Trigger", "2026-04-03 03:57:58pm"),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview mock terminal UI screens.")
    parser.add_argument(
        "screen",
        choices=["preflight", "launch", "runtime-delay", "runtime-active", "runtime-done", "runtime"],
        help="Which mock screen to render.",
    )
    args = parser.parse_args()

    console = Console()
    if args.screen == "preflight":
        console.print(_preflight_screen())
    elif args.screen == "launch":
        console.print(_launch_screen())
    elif args.screen in {"runtime", "runtime-active"}:
        console.print(_runtime_screen("active"))
    elif args.screen == "runtime-done":
        console.print(_runtime_screen("done"))
    else:
        console.print(_runtime_screen("delay"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

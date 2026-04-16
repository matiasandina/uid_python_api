#!/usr/bin/env python3
"""Generate synthetic data and run through the trigger pipeline and UI."""

from __future__ import annotations

import argparse
import random
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

import yaml

from data_parser import ParsedReading
from device_manager import DeviceManager
from console_display import ConsoleDisplay
from session_metadata import write_session_metadata
from main import load_config, validate_config


def load_scenario(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("Scenario must be a YAML mapping at the top level.")
    return data


def in_missing_window(elapsed_minutes: float, missing: List[Dict[str, Any]]) -> bool:
    for window in missing:
        start = window.get("start_min", 0)
        end = window.get("end_min", 0)
        if start <= 0 and end <= 0:
            continue
        if start <= elapsed_minutes <= end:
            return True
    return False


def generate_readings(scenario: Dict[str, Any], start_time: datetime) -> List[Dict[str, Any]]:
    duration_seconds = float(scenario.get("duration_seconds", 3600))
    interval_seconds = float(scenario.get("interval_seconds", 5.0))
    seed = scenario.get("seed", 123)
    random.seed(seed)

    animals = scenario.get("animals", [])
    readings: List[Dict[str, Any]] = []

    total_steps = int(duration_seconds // interval_seconds)
    for step in range(total_steps):
        now = start_time + timedelta(seconds=step * interval_seconds)
        elapsed_minutes = (step * interval_seconds) / 60.0
        for animal in animals:
            animal_id = animal.get("id")
            if not animal_id:
                continue
            missing = animal.get("missing_windows", [])
            if in_missing_window(elapsed_minutes, missing):
                continue

            base = float(animal.get("base_temp", 37.0))
            trend_per_min = float(animal.get("trend_per_min", 0.0))
            noise_std = float(animal.get("noise_std", 0.1))
            zone = int(animal.get("zone", 1))

            temp = base + trend_per_min * elapsed_minutes + random.gauss(0.0, noise_std)

            readings.append(
                {
                    "timestamp": now,
                    "animal_id": str(animal_id),
                    "temperature": temp,
                    "zone": zone,
                }
            )

    return readings


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate synthetic data")
    parser.add_argument("--config", help="Optional overlay YAML config file")
    parser.add_argument("--scenario", required=True, help="Scenario YAML file")
    parser.add_argument("--speed", type=float, default=100.0, help="Playback speed (e.g., 100x)")
    parser.add_argument("--device-name", default="Sim", help="Logical device name")
    args = parser.parse_args()

    config = validate_config(load_config(args.config, require_local=False))
    scenario = load_scenario(args.scenario)

    manager = DeviceManager(
        output_directory=config["output_directory"],
        averaging_window_seconds=config["data"]["averaging_window_seconds"],
        display_interval_seconds=config["display"]["display_interval_seconds"],
        stale_timeout_seconds=config["network"]["stale_timeout_seconds"],
        reconnect_delay_seconds=config["network"]["reconnect_delay_seconds"],
        health_check_interval_seconds=config["network"]["health_check_interval_seconds"],
        quiet_mode=True,
        session_description=config.get("session_description"),
        closed_loop_config=config.get("closed_loop", {"rules": []}),
        stimulus_config=config["stimulus"],
    )

    display = ConsoleDisplay(
        manager=manager,
        output_directory=manager._session_folder,
        refresh_hz=config["display"]["refresh_hz"],
        missing_animal_seconds=None,
    )

    start_time = datetime.now()
    manager.start_control_only()
    display.start()

    try:
        readings = generate_readings(scenario, start_time)
        packet = 0
        last_time = None
        for item in readings:
            ts = item["timestamp"]
            if last_time is None:
                last_time = ts
            else:
                delta = (ts - last_time).total_seconds()
                if delta > 0:
                    time.sleep(delta / args.speed)
                last_time = ts

            packet += 1
            ingest_ts = datetime.now()
            reading = ParsedReading(
                packet_number=packet,
                zone=item["zone"],
                animal_id=item["animal_id"],
                temperature=item["temperature"],
                raw_data="",
                timestamp=ingest_ts,
            )
            manager.ingest_reading(args.device_name, reading)
    finally:
        display.stop()
        manager.stop_control_only()
        end_time = datetime.now()
        write_session_metadata(
            output_directory=config["output_directory"],
            session_folder=manager._session_folder,
            config=config,
            mode="simulate",
            start_time=start_time,
            end_time=end_time,
            trigger_events=manager.get_trigger_events(),
            missing_events=manager.get_missing_events(),
            source=args.scenario,
            session_label=config.get("session_description"),
        )
        manager.stop_all()

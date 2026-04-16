#!/usr/bin/env python3
"""Replay recorded CSV data through the trigger pipeline and UI."""

from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime
from typing import Optional

from data_parser import ParsedReading
from device_manager import DeviceManager
from console_display import ConsoleDisplay
from session_metadata import write_session_metadata
from main import load_config, validate_config


def parse_timestamp(value: str) -> Optional[datetime]:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _first_present(row: dict, keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        if key in row and row[key] is not None:
            value = str(row[key]).strip()
            if value:
                return value
    return default


def _safe_int(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _safe_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def replay_csv(path: str, speed: float, device_name: str, manager: DeviceManager) -> None:
    with open(path, "r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    if not rows:
        return

    first_ts = None
    for row in rows:
        ts = parse_timestamp(_first_present(row, ("DateTime", "Date")))
        if ts is not None:
            first_ts = ts
            break
    if first_ts is None:
        return

    sim_start = datetime.now()
    packet = 0
    last_time = None
    for row in rows:
        ts = parse_timestamp(_first_present(row, ("DateTime", "Date")))
        if ts is None:
            continue
        sim_ts = sim_start + (ts - first_ts)
        if last_time is None:
            last_time = sim_ts
        else:
            delta = (sim_ts - last_time).total_seconds()
            if delta > 0:
                time.sleep(delta / speed)
            last_time = sim_ts

        packet += 1
        ingest_ts = datetime.now()
        zone_value = _first_present(row, ("Zone",), default="0")
        uid_value = _first_present(row, ("UID", "RFID"), default="")
        temp_value = _first_present(row, ("Temperature",), default="0.0")
        reading = ParsedReading(
            packet_number=packet,
            zone=_safe_int(zone_value, default=0),
            animal_id=uid_value,
            temperature=_safe_float(temp_value, default=0.0),
            raw_data="",
            timestamp=ingest_ts,
        )
        manager.ingest_reading(device_name, reading)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay recorded CSV data")
    parser.add_argument("--config", help="Optional overlay YAML config file")
    parser.add_argument("--csv", required=True, help="CSV file to replay")
    parser.add_argument("--speed", type=float, default=100.0, help="Playback speed (e.g., 100x)")
    parser.add_argument("--device-name", default="Replay", help="Logical device name")
    args = parser.parse_args()

    config = validate_config(load_config(args.config, require_local=False))

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
        replay_csv(args.csv, args.speed, args.device_name, manager)
    finally:
        display.stop()
        manager.stop_control_only()
        end_time = datetime.now()
        write_session_metadata(
            output_directory=config["output_directory"],
            session_folder=manager._session_folder,
            config=config,
            mode="replay",
            start_time=start_time,
            end_time=end_time,
            trigger_events=manager.get_trigger_events(),
            missing_events=manager.get_missing_events(),
            source=args.csv,
            session_label=config.get("session_description"),
        )
        manager.stop_all()

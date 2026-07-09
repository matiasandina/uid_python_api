#!/usr/bin/env python3
"""Create a one-shot temperature plot from an active or completed session CSV.

The reader is intentionally conservative for active acquisition files:
- it opens CSV files read-only;
- it ignores an incomplete final line;
- it filters out the most recent safety-lag window by default.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import statistics
import sys
import webbrowser
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TIMESTAMP_COLUMNS = ("DateTime", "Date", "timestamp", "Timestamp")
ANIMAL_COLUMNS = ("UID", "RFID", "animal_id", "AnimalID")
TEMPERATURE_COLUMNS = ("Temperature", "Temp", "temperature", "temperature_c")
ZONE_COLUMNS = ("Zone", "zone")


@dataclass(frozen=True)
class Reading:
    timestamp: datetime
    animal_id: str
    temperature_c: float
    source_file: str
    zone: str


@dataclass(frozen=True)
class StimWindow:
    start: datetime
    stop: datetime
    animal_id: str
    stimulus_id: str
    channel_name: str
    reason: str
    source: str


def _first_present(row: dict[str, str], keys: Iterable[str], default: str = "") -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def _read_complete_csv_text(path: Path) -> str:
    data = path.read_bytes()
    if not data:
        return ""
    text = data.decode("utf-8", errors="replace")
    if not text.endswith(("\n", "\r")):
        cut = max(text.rfind("\n"), text.rfind("\r"))
        if cut < 0:
            return ""
        text = text[: cut + 1]
    return text


def _parse_timestamp(value: str, local_tz: ZoneInfo) -> datetime:
    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=local_tz)
    return parsed.astimezone(local_tz)


def _discover_csvs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")
    return sorted(
        path
        for path in input_path.glob("*.csv")
        if path.is_file() and not path.name.startswith(".")
    )


def _load_readings(csv_paths: list[Path], local_tz: ZoneInfo, cutoff: datetime) -> list[Reading]:
    readings: list[Reading] = []
    for csv_path in csv_paths:
        text = _read_complete_csv_text(csv_path)
        if not text.strip():
            continue
        reader = csv.DictReader(StringIO(text))
        for raw_row in reader:
            try:
                timestamp = _parse_timestamp(_first_present(raw_row, TIMESTAMP_COLUMNS), local_tz)
                if timestamp > cutoff:
                    continue
                animal_id = _first_present(raw_row, ANIMAL_COLUMNS).strip().upper()
                if not animal_id:
                    continue
                temperature_text = _first_present(raw_row, TEMPERATURE_COLUMNS)
                temperature_c = float(temperature_text)
            except (TypeError, ValueError):
                continue
            readings.append(
                Reading(
                    timestamp=timestamp,
                    animal_id=animal_id,
                    temperature_c=temperature_c,
                    source_file=csv_path.name,
                    zone=_first_present(raw_row, ZONE_COLUMNS),
                )
            )
    return sorted(readings, key=lambda item: (item.timestamp, item.animal_id))


def _metadata_path(input_path: Path, explicit: Path | None) -> Path | None:
    if explicit:
        return explicit if explicit.exists() else None
    directory = input_path if input_path.is_dir() else input_path.parent
    candidate = directory / "session.yaml"
    return candidate if candidate.exists() else None


def _resolve_local_timezone(explicit: str | None, metadata: dict[str, Any]) -> ZoneInfo:
    if explicit:
        return ZoneInfo(explicit)
    session = metadata.get("session", {}) if isinstance(metadata, dict) else {}
    config = metadata.get("config", {}) if isinstance(metadata, dict) else {}
    stimulus = config.get("stimulus", {}) if isinstance(config, dict) else {}
    start_cfg = stimulus.get("start", {}) if isinstance(stimulus, dict) else {}
    for candidate in (
        session.get("local_timezone") if isinstance(session, dict) else None,
        config.get("local_timezone") if isinstance(config, dict) else None,
        start_cfg.get("timezone") if isinstance(start_cfg, dict) else None,
    ):
        if candidate and str(candidate).strip():
            return ZoneInfo(str(candidate).strip())
    return datetime.now().astimezone().tzinfo or ZoneInfo("UTC")


def _load_metadata(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return payload if isinstance(payload, dict) else {}


def _load_yaml_file(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return payload if isinstance(payload, dict) else {}


def _event_channels(event: dict[str, Any]) -> list[str]:
    meta = event.get("meta", {})
    if not isinstance(meta, dict):
        return [""]
    channels = meta.get("channels", [])
    if not isinstance(channels, list):
        return [""]
    cleaned = [str(channel).strip() for channel in channels if str(channel).strip()]
    return cleaned or [""]


def _event_assignments(event: dict[str, Any], channel_name: str) -> tuple[str, list[str]]:
    meta = event.get("meta", {})
    if not isinstance(meta, dict):
        return "", []
    assignments = meta.get("assignments", [])
    if not isinstance(assignments, list):
        return "", []
    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        if str(assignment.get("channel") or "").strip() != channel_name:
            continue
        animal_ids = [
            str(item).strip().upper()
            for item in assignment.get("assigned_animal_ids", [])
            if str(item).strip()
        ]
        return str(assignment.get("id") or "").strip(), animal_ids
    return "", []


def _load_stim_windows(metadata: dict[str, Any], local_tz: ZoneInfo, cutoff: datetime) -> list[StimWindow]:
    grouped = metadata.get("triggers_by_animal", {})
    if not isinstance(grouped, dict):
        return []

    events: list[tuple[str, dict[str, Any], datetime]] = []
    for animal_id, animal_events in grouped.items():
        if not isinstance(animal_events, list):
            continue
        for event in animal_events:
            if not isinstance(event, dict):
                continue
            try:
                timestamp = _parse_timestamp(str(event.get("timestamp") or ""), local_tz)
            except ValueError:
                continue
            if timestamp <= cutoff:
                events.append((str(animal_id).strip().upper(), event, timestamp))

    active: dict[tuple[str, str, str], tuple[str, dict[str, Any], datetime]] = {}
    windows: list[StimWindow] = []

    def append_window(
        start_animal_id: str,
        start_event: dict[str, Any],
        start_timestamp: datetime,
        stop_timestamp: datetime,
        channel_name: str,
        stop_reason: str,
    ) -> None:
        if stop_timestamp <= start_timestamp:
            return
        assignment_id, assigned_animals = _event_assignments(start_event, channel_name)
        shade_animal = "__open_loop__" if start_animal_id == "__OPEN_LOOP__" else start_animal_id
        if assigned_animals:
            shade_animal = ",".join(assigned_animals)
        stimulus_id = str(start_event.get("stimulus_id") or "").strip()
        reason = f"{start_event.get('reason', '')} -> {stop_reason}".strip()
        if assignment_id:
            reason = f"{assignment_id}: {reason}"
        windows.append(
            StimWindow(
                start=start_timestamp,
                stop=stop_timestamp,
                animal_id=shade_animal,
                stimulus_id=stimulus_id,
                channel_name=channel_name,
                reason=reason,
                source="recorded",
            )
        )

    for animal_id, event, timestamp in sorted(events, key=lambda item: item[2]):
        action = str(event.get("action") or "").strip().lower()
        stimulus_id = str(event.get("stimulus_id") or "").strip()
        for channel_name in _event_channels(event):
            key = (animal_id, stimulus_id, channel_name)
            if action == "start":
                active[key] = (animal_id, event, timestamp)
            elif action == "stop" and key in active:
                start_animal_id, start_event, start_timestamp = active.pop(key)
                append_window(
                    start_animal_id=start_animal_id,
                    start_event=start_event,
                    start_timestamp=start_timestamp,
                    stop_timestamp=timestamp,
                    channel_name=channel_name,
                    stop_reason=str(event.get("reason", "")),
                )
    for _, (start_animal_id, start_event, start_timestamp) in active.items():
        for channel_name in _event_channels(start_event):
            append_window(
                start_animal_id=start_animal_id,
                start_event=start_event,
                start_timestamp=start_timestamp,
                stop_timestamp=cutoff,
                channel_name=channel_name,
                stop_reason="active at cutoff",
            )
    return windows


def _stimulus_config(metadata: dict[str, Any], explicit_config: dict[str, Any]) -> dict[str, Any]:
    config = explicit_config or metadata.get("config", {})
    if not isinstance(config, dict):
        return {}
    stimulus = config.get("stimulus", {})
    return stimulus if isinstance(stimulus, dict) else {}


def _open_loop_channels(stimulus: dict[str, Any]) -> list[str]:
    assignments = stimulus.get("open_loop_assignments", [])
    channels: list[str] = []
    if isinstance(assignments, list):
        for assignment in assignments:
            if not isinstance(assignment, dict):
                continue
            channel = str(assignment.get("channel") or "").strip()
            if channel:
                channels.append(channel)
    if not channels:
        target_channels = stimulus.get("target_channels", [])
        if isinstance(target_channels, list):
            channels = [str(channel).strip() for channel in target_channels if str(channel).strip()]
    return sorted(dict.fromkeys(channels))


def _planned_open_loop_start(session_start: datetime, stimulus: dict[str, Any], local_tz: ZoneInfo) -> datetime | None:
    start_cfg = stimulus.get("start", {})
    if not isinstance(start_cfg, dict):
        start_cfg = {}

    if stimulus.get("start_delay_seconds") is not None:
        try:
            return session_start + timedelta(seconds=float(stimulus.get("start_delay_seconds", 0.0)))
        except (TypeError, ValueError):
            return None

    mode = str(start_cfg.get("mode", "immediate")).strip().lower()
    if mode in ("", "immediate"):
        return session_start
    if mode == "delay":
        try:
            return session_start + timedelta(seconds=float(start_cfg.get("delay_seconds", 0.0)))
        except (TypeError, ValueError):
            return None
    if mode == "clock":
        hhmm = str(start_cfg.get("at_hhmm") or "").strip()
        try:
            hour_text, minute_text = hhmm.split(":", 1)
            scheduled = session_start.astimezone(local_tz).replace(
                hour=int(hour_text),
                minute=int(minute_text),
                second=0,
                microsecond=0,
            )
        except (TypeError, ValueError):
            return None
        if scheduled < session_start and bool(start_cfg.get("rollback_next_day", False)):
            scheduled = scheduled + timedelta(days=1)
        return scheduled
    return None


def _load_planned_open_loop_windows(
    config: dict[str, Any],
    metadata: dict[str, Any],
    local_tz: ZoneInfo,
    session_start: datetime,
    cutoff: datetime,
) -> list[StimWindow]:
    stimulus = _stimulus_config(metadata, config)
    if not stimulus:
        return []
    if str(stimulus.get("control_mode") or "").strip().lower() != "open_loop":
        return []
    if not bool(stimulus.get("enabled", False)):
        return []

    channels = _open_loop_channels(stimulus)
    if not channels:
        return []

    try:
        run_seconds = float(stimulus.get("run_for_minutes", 0.0)) * 60.0
    except (TypeError, ValueError):
        return []
    if run_seconds <= 0:
        return []

    train_cfg = stimulus.get("train", {})
    if not isinstance(train_cfg, dict):
        train_cfg = {}
    try:
        on_seconds = float(train_cfg.get("on_seconds", stimulus.get("window_on_seconds", 1.0)))
        off_seconds = float(train_cfg.get("off_seconds", 0.0))
    except (TypeError, ValueError):
        return []
    if on_seconds <= 0 or off_seconds < 0:
        return []

    scheduled_start = _planned_open_loop_start(session_start, stimulus, local_tz)
    if scheduled_start is None:
        return []
    run_end = scheduled_start + timedelta(seconds=run_seconds)
    if scheduled_start > cutoff:
        return []

    windows: list[StimWindow] = []
    if off_seconds == 0:
        starts = [scheduled_start]
        stops = [min(run_end, cutoff)]
    else:
        starts = []
        stops = []
        cursor = scheduled_start
        while cursor < run_end and cursor <= cutoff:
            starts.append(cursor)
            stops.append(min(cursor + timedelta(seconds=on_seconds), run_end, cutoff))
            cursor = cursor + timedelta(seconds=on_seconds + off_seconds)

    mode = str(stimulus.get("mode") or "").strip().lower()
    reason = "planned open_loop from config"
    if mode and mode != "laser":
        reason = f"planned open_loop from config; stimulus.mode={mode}"
    for start, stop in zip(starts, stops):
        if stop <= start:
            continue
        for channel_name in channels:
            windows.append(
                StimWindow(
                    start=start,
                    stop=stop,
                    animal_id="__open_loop__",
                    stimulus_id="__open_loop__",
                    channel_name=channel_name,
                    reason=reason,
                    source="planned",
                )
            )
    return windows


def _closed_loop_rules(config: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    source = config or metadata.get("config", {})
    if not isinstance(source, dict):
        return []
    closed_loop = source.get("closed_loop", {})
    if not isinstance(closed_loop, dict):
        return []
    rules = closed_loop.get("rules", [])
    return [rule for rule in rules if isinstance(rule, dict)] if isinstance(rules, list) else []


def _reading_payload(reading: Reading) -> dict[str, Any]:
    return {
        "timestamp": reading.timestamp,
        "temperature": reading.temperature_c,
        "zone": reading.zone,
        "packet_number": None,
        "device_name": Path(reading.source_file).stem,
    }


def _rule_applies_to_reading(rule: dict[str, Any], reading: Reading) -> bool:
    assigned_ids = {
        str(animal_id).strip().upper()
        for animal_id in rule.get("assigned_animal_ids", [])
        if str(animal_id).strip()
    }
    if assigned_ids and reading.animal_id not in assigned_ids:
        return False
    devices = {str(device).strip() for device in rule.get("devices", []) if str(device).strip()}
    if devices and Path(reading.source_file).stem not in devices and reading.source_file not in devices:
        return False
    return True


def _load_inferred_closed_loop_windows(
    config: dict[str, Any],
    metadata: dict[str, Any],
    readings: list[Reading],
    cutoff: datetime,
) -> list[StimWindow]:
    stimulus = _stimulus_config(metadata, config)
    if str(stimulus.get("control_mode") or "").strip().lower() != "closed_loop":
        return []
    if not bool(stimulus.get("enabled", False)):
        return []

    rules = _closed_loop_rules(config, metadata)
    if not rules:
        return []

    try:
        from trigger_scheduler import load_classifier
    except Exception:
        return []

    windows: list[StimWindow] = []
    by_animal: dict[str, list[Reading]] = defaultdict(list)
    for reading in readings:
        by_animal[reading.animal_id].append(reading)

    for rule in rules:
        classifier_cfg = rule.get("classifier", {})
        if not isinstance(classifier_cfg, dict):
            continue
        try:
            classifier = load_classifier(str(classifier_cfg["plugin"]))
            window_seconds = float(classifier_cfg["clf_data_input_window_seconds"])
        except Exception:
            continue

        mode = str(classifier_cfg.get("mode", "window")).strip().lower()
        classifier_config = classifier_cfg.get("config", {})
        if not isinstance(classifier_config, dict):
            classifier_config = {}
        rule_id = str(rule.get("id") or "").strip()
        channels = [
            str(channel).strip()
            for channel in (rule.get("outputs", {}) or {}).get("laser_channels", [])
            if str(channel).strip()
        ]
        if not channels:
            channels = [""]
        pulse_seconds = max(0.1, float(stimulus.get("window_on_seconds", 1.0) or 1.0))

        active_by_animal: dict[str, tuple[datetime, str, str]] = {}
        candidate_readings = [reading for reading in readings if _rule_applies_to_reading(rule, reading)]
        for reading in candidate_readings:
            animal_readings = by_animal[reading.animal_id]
            start_cutoff = reading.timestamp - timedelta(seconds=window_seconds)
            window_payload = [
                _reading_payload(item)
                for item in animal_readings
                if start_cutoff <= item.timestamp <= reading.timestamp and _rule_applies_to_reading(rule, item)
            ]
            if not window_payload:
                continue
            try:
                result = classifier(reading.animal_id, window_payload, reading.timestamp, classifier_config)
            except Exception:
                continue
            result_dict = result if isinstance(result, dict) else {}
            condition_true = bool(result_dict.get("condition_true", result_dict.get("trigger", False)))
            stimulus_id = str(result_dict.get("stimulus_id", classifier_config.get("stimulus_id", "")))
            reason = str(result_dict.get("reason", "closed_loop replay"))

            if mode == "window":
                previous = active_by_animal.get(reading.animal_id)
                if condition_true and previous is None:
                    active_by_animal[reading.animal_id] = (reading.timestamp, stimulus_id, reason)
                elif not condition_true and previous is not None:
                    start, start_stimulus_id, start_reason = active_by_animal.pop(reading.animal_id)
                    for channel_name in channels:
                        windows.append(
                            StimWindow(
                                start=start,
                                stop=reading.timestamp,
                                animal_id=reading.animal_id,
                                stimulus_id=start_stimulus_id,
                                channel_name=channel_name,
                                reason=f"inferred closed_loop: {start_reason} -> {reason}",
                                source="inferred",
                            )
                        )
            elif result_dict.get("trigger"):
                for channel_name in channels:
                    windows.append(
                        StimWindow(
                            start=reading.timestamp,
                            stop=min(reading.timestamp + timedelta(seconds=pulse_seconds), cutoff),
                            animal_id=reading.animal_id,
                            stimulus_id=stimulus_id,
                            channel_name=channel_name,
                            reason=f"inferred closed_loop pulse: {reason}",
                            source="inferred",
                        )
                    )

        if mode == "window":
            for animal_id, (start, stimulus_id, reason) in active_by_animal.items():
                if cutoff > start:
                    for channel_name in channels:
                        windows.append(
                            StimWindow(
                                start=start,
                                stop=cutoff,
                                animal_id=animal_id,
                                stimulus_id=stimulus_id,
                                channel_name=channel_name,
                                reason=f"inferred closed_loop: {reason} -> active at cutoff",
                                source="inferred",
                            )
                        )
    return windows


def _windows_overlap(left: StimWindow, right: StimWindow) -> bool:
    return left.start < right.stop and right.start < left.stop


def _append_non_overlapping_windows(windows: list[StimWindow], candidates: list[StimWindow]) -> None:
    """Add diagnostic windows unless a recorded window already covers the same animal/channel span."""
    for candidate in candidates:
        if candidate.source not in {"planned", "inferred"}:
            windows.append(candidate)
            continue
        duplicate = any(
            existing.source == "recorded"
            and existing.animal_id == candidate.animal_id
            and existing.channel_name == candidate.channel_name
            and _windows_overlap(existing, candidate)
            for existing in windows
        )
        if not duplicate:
            windows.append(candidate)


def _load_snapshot_windows(
    config: dict[str, Any],
    metadata: dict[str, Any],
    local_tz: ZoneInfo,
    readings: list[Reading],
    cutoff: datetime,
) -> list[StimWindow]:
    windows = _load_stim_windows(metadata, local_tz, cutoff)
    if not readings:
        return windows

    session_start = min(reading.timestamp for reading in readings)
    planned = _load_planned_open_loop_windows(
        config=config,
        metadata=metadata,
        local_tz=local_tz,
        session_start=session_start,
        cutoff=cutoff,
    )
    _append_non_overlapping_windows(windows, planned)

    inferred = _load_inferred_closed_loop_windows(
        config=config,
        metadata=metadata,
        readings=readings,
        cutoff=cutoff,
    )
    _append_non_overlapping_windows(windows, inferred)
    return windows


def _downsample_points(points: list[Reading], max_points: int) -> list[Reading]:
    if max_points <= 0 or len(points) <= max_points:
        return points
    bucket_count = max(1, max_points // 2)
    bucket_size = math.ceil(len(points) / bucket_count)
    sampled: list[Reading] = []
    for start in range(0, len(points), bucket_size):
        bucket = points[start : start + bucket_size]
        if not bucket:
            continue
        low = min(bucket, key=lambda item: item.temperature_c)
        high = max(bucket, key=lambda item: item.temperature_c)
        if low.timestamp <= high.timestamp:
            sampled.extend([low, high])
        else:
            sampled.extend([high, low])
    return sorted(sampled[:max_points], key=lambda item: item.timestamp)


def _build_payload(readings: list[Reading], windows: list[StimWindow], max_points_per_animal: int) -> dict[str, Any]:
    grouped: dict[str, list[Reading]] = defaultdict(list)
    for reading in readings:
        grouped[reading.animal_id].append(reading)

    animals: dict[str, dict[str, Any]] = {}
    for animal_id, animal_readings in sorted(grouped.items()):
        sampled = _downsample_points(animal_readings, max_points_per_animal)
        temperatures = [item.temperature_c for item in animal_readings]
        animals[animal_id] = {
            "points": [
                [int(item.timestamp.timestamp() * 1000), item.temperature_c]
                for item in sampled
            ],
            "raw_count": len(animal_readings),
            "plotted_count": len(sampled),
            "min": min(temperatures),
            "max": max(temperatures),
            "mean": statistics.fmean(temperatures),
            "latest": animal_readings[-1].temperature_c,
            "last_seen_ms": int(animal_readings[-1].timestamp.timestamp() * 1000),
        }

    return {
        "animals": animals,
        "windows": [
            {
                "start_ms": int(window.start.timestamp() * 1000),
                "stop_ms": int(window.stop.timestamp() * 1000),
                "animal_id": window.animal_id,
                "stimulus_id": window.stimulus_id,
                "channel_name": window.channel_name,
                "reason": window.reason,
                "source": window.source,
            }
            for window in windows
        ],
    }


def _html_document(payload: dict[str, Any], title: str, subtitle: str) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{
  color-scheme: light;
  font-family: Arial, Helvetica, sans-serif;
  --ink: #1f2933;
  --muted: #617080;
  --line: #d8dee6;
  --panel: #f7f9fb;
  --accent: #2563eb;
}}
body {{
  margin: 0;
  color: var(--ink);
  background: #ffffff;
}}
header {{
  padding: 16px 20px 8px;
  border-bottom: 1px solid var(--line);
}}
h1 {{
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0;
}}
.subtitle {{
  color: var(--muted);
  font-size: 13px;
}}
.toolbar {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  padding: 12px 20px;
  background: var(--panel);
  border-bottom: 1px solid var(--line);
  align-items: end;
}}
label {{
  display: block;
  font-size: 12px;
  font-weight: 700;
  color: #415160;
  margin-bottom: 4px;
}}
input, button {{
  font: inherit;
  font-size: 13px;
}}
input[type="datetime-local"] {{
  width: 100%;
  box-sizing: border-box;
  padding: 6px 8px;
  border: 1px solid #b9c3cf;
  border-radius: 4px;
  background: white;
}}
button {{
  padding: 7px 10px;
  border: 1px solid #9fb0c2;
  border-radius: 4px;
  background: white;
  cursor: pointer;
}}
button:hover {{
  border-color: var(--accent);
}}
.quick-buttons {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}}
.layout {{
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  min-height: calc(100vh - 139px);
}}
aside {{
  border-right: 1px solid var(--line);
  padding: 12px 14px;
  overflow: auto;
}}
.animal-row {{
  display: grid;
  grid-template-columns: 18px 1fr;
  gap: 6px;
  align-items: start;
  padding: 6px 0;
  border-bottom: 1px solid #eef2f6;
  font-size: 13px;
}}
.animal-meta {{
  color: var(--muted);
  font-size: 11px;
  margin-top: 2px;
  line-height: 1.35;
}}
main {{
  min-width: 0;
  padding: 12px;
}}
#plot {{
  width: 100%;
  height: calc(100vh - 175px);
  min-height: 420px;
  display: block;
  border: 1px solid var(--line);
  background: white;
}}
#status {{
  margin-top: 8px;
  color: var(--muted);
  font-size: 12px;
}}
@media (max-width: 780px) {{
  .layout {{
    grid-template-columns: 1fr;
  }}
  aside {{
    border-right: 0;
    border-bottom: 1px solid var(--line);
    max-height: 210px;
  }}
  #plot {{
    height: 65vh;
  }}
}}
</style>
</head>
<body>
<header>
  <h1>{html.escape(title)}</h1>
  <div class="subtitle">{html.escape(subtitle)}</div>
</header>
<section class="toolbar">
  <div>
    <label for="start">Start</label>
    <input id="start" type="datetime-local" step="1">
  </div>
  <div>
    <label for="end">End</label>
    <input id="end" type="datetime-local" step="1">
  </div>
  <div>
    <label>Range</label>
    <div class="quick-buttons">
      <button data-range-hours="1">1 h</button>
      <button data-range-hours="6">6 h</button>
      <button data-range-hours="12">12 h</button>
      <button data-range-hours="24">24 h</button>
      <button id="fullRange">Full</button>
    </div>
  </div>
  <div>
    <label>Display</label>
    <div class="quick-buttons">
      <button id="allAnimals">All animals</button>
      <button id="noAnimals">None</button>
      <button id="toggleStim">Stim shading</button>
    </div>
  </div>
</section>
<section class="layout">
  <aside>
    <label>RFID filters</label>
    <div id="animals"></div>
  </aside>
  <main>
    <canvas id="plot"></canvas>
    <div id="status"></div>
  </main>
</section>
<script>
const payload = {payload_json};
const colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22"];
const animalIds = Object.keys(payload.animals).sort();
const enabled = new Set(animalIds);
let showStim = true;

function allPoints() {{
  return animalIds.flatMap(id => payload.animals[id].points);
}}

function pad(n) {{
  return String(n).padStart(2, "0");
}}

function toLocalInput(ms) {{
  const d = new Date(ms);
  return `${{d.getFullYear()}}-${{pad(d.getMonth()+1)}}-${{pad(d.getDate())}}T${{pad(d.getHours())}}:${{pad(d.getMinutes())}}:${{pad(d.getSeconds())}}`;
}}

function fromLocalInput(value) {{
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}}

const points = allPoints();
const dataMinX = points.length ? Math.min(...points.map(p => p[0])) : Date.now();
const dataMaxX = points.length ? Math.max(...points.map(p => p[0])) : Date.now();
document.getElementById("start").value = toLocalInput(dataMinX);
document.getElementById("end").value = toLocalInput(dataMaxX);

function renderAnimalList() {{
  const container = document.getElementById("animals");
  container.innerHTML = "";
  animalIds.forEach((id, index) => {{
    const info = payload.animals[id];
    const row = document.createElement("div");
    row.className = "animal-row";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = enabled.has(id);
    checkbox.addEventListener("change", () => {{
      if (checkbox.checked) enabled.add(id); else enabled.delete(id);
      draw();
    }});
    const text = document.createElement("div");
    const color = colors[index % colors.length];
    text.innerHTML = `<strong style="color:${{color}}">${{id}}</strong><div class="animal-meta">latest ${{info.latest.toFixed(2)}} C | mean ${{info.mean.toFixed(2)}} C<br>${{info.raw_count}} rows, ${{info.plotted_count}} plotted</div>`;
    row.appendChild(checkbox);
    row.appendChild(text);
    container.appendChild(row);
  }});
}}

function niceTime(ms, spanMs) {{
  const d = new Date(ms);
  if (spanMs > 36 * 3600 * 1000) {{
    return `${{d.getMonth()+1}}/${{d.getDate()}} ${{pad(d.getHours())}}:${{pad(d.getMinutes())}}`;
  }}
  return `${{pad(d.getHours())}}:${{pad(d.getMinutes())}}`;
}}

function draw() {{
  const canvas = document.getElementById("plot");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = rect.width;
  const h = rect.height;
  ctx.clearRect(0, 0, w, h);

  const margin = {{left: 58, right: 20, top: 18, bottom: 46}};
  const plotW = w - margin.left - margin.right;
  const plotH = h - margin.top - margin.bottom;
  const x0 = fromLocalInput(document.getElementById("start").value) ?? dataMinX;
  const x1 = fromLocalInput(document.getElementById("end").value) ?? dataMaxX;
  const minX = Math.min(x0, x1);
  const maxX = Math.max(x0, x1);
  const visible = [];
  animalIds.forEach(id => {{
    if (!enabled.has(id)) return;
    for (const p of payload.animals[id].points) {{
      if (p[0] >= minX && p[0] <= maxX) visible.push(p[1]);
    }}
  }});
  if (!visible.length) {{
    ctx.fillStyle = "#617080";
    ctx.font = "14px Arial";
    ctx.fillText("No visible temperature points for selected filters.", margin.left, margin.top + 24);
    document.getElementById("status").textContent = "No visible points.";
    return;
  }}
  let minY = Math.min(...visible);
  let maxY = Math.max(...visible);
  const padY = Math.max(0.2, (maxY - minY) * 0.08);
  minY -= padY;
  maxY += padY;
  if (minY === maxY) {{ minY -= 1; maxY += 1; }}
  const xScale = ms => margin.left + ((ms - minX) / (maxX - minX || 1)) * plotW;
  const yScale = value => margin.top + (1 - ((value - minY) / (maxY - minY))) * plotH;

  ctx.fillStyle = "#ffffff";
  ctx.fillRect(margin.left, margin.top, plotW, plotH);

  if (showStim) {{
    for (const win of payload.windows) {{
      const start = Math.max(win.start_ms, minX);
      const stop = Math.min(win.stop_ms, maxX);
      if (stop < minX || start > maxX || stop <= start) continue;
      ctx.fillStyle = win.source === "planned" ? "rgba(37, 99, 235, 0.14)" : (win.source === "inferred" ? "rgba(22, 163, 74, 0.14)" : "rgba(245, 158, 11, 0.18)");
      ctx.fillRect(xScale(start), margin.top, Math.max(1, xScale(stop) - xScale(start)), plotH);
    }}
  }}

  ctx.strokeStyle = "#e5eaf0";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#617080";
  ctx.font = "11px Arial";
  for (let i = 0; i <= 5; i++) {{
    const y = margin.top + (plotH * i / 5);
    const value = maxY - ((maxY - minY) * i / 5);
    ctx.beginPath();
    ctx.moveTo(margin.left, y);
    ctx.lineTo(margin.left + plotW, y);
    ctx.stroke();
    ctx.fillText(value.toFixed(1), 8, y + 4);
  }}
  const span = maxX - minX;
  for (let i = 0; i <= 6; i++) {{
    const x = margin.left + (plotW * i / 6);
    const ms = minX + (span * i / 6);
    ctx.strokeStyle = "#eef2f6";
    ctx.beginPath();
    ctx.moveTo(x, margin.top);
    ctx.lineTo(x, margin.top + plotH);
    ctx.stroke();
    ctx.fillStyle = "#617080";
    ctx.fillText(niceTime(ms, span), x - 24, margin.top + plotH + 22);
  }}

  ctx.strokeStyle = "#9aa8b6";
  ctx.beginPath();
  ctx.rect(margin.left, margin.top, plotW, plotH);
  ctx.stroke();

  animalIds.forEach((id, index) => {{
    if (!enabled.has(id)) return;
    const pts = payload.animals[id].points.filter(p => p[0] >= minX && p[0] <= maxX);
    if (!pts.length) return;
    ctx.strokeStyle = colors[index % colors.length];
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    pts.forEach((p, i) => {{
      const x = xScale(p[0]);
      const y = yScale(p[1]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }});
    ctx.stroke();
  }});

  const visibleCount = animalIds.reduce((acc, id) => acc + (enabled.has(id) ? payload.animals[id].points.filter(p => p[0] >= minX && p[0] <= maxX).length : 0), 0);
  const planned = payload.windows.filter(win => win.source === "planned").length;
  const recorded = payload.windows.filter(win => win.source === "recorded").length;
  const inferred = payload.windows.filter(win => win.source === "inferred").length;
  document.getElementById("status").textContent = `${{enabled.size}} animals visible | ${{visibleCount}} plotted points in range | ${{recorded}} recorded/commanded windows | ${{planned}} planned windows | ${{inferred}} inferred windows`;
}}

document.getElementById("start").addEventListener("change", draw);
document.getElementById("end").addEventListener("change", draw);
document.getElementById("allAnimals").addEventListener("click", () => {{ animalIds.forEach(id => enabled.add(id)); renderAnimalList(); draw(); }});
document.getElementById("noAnimals").addEventListener("click", () => {{ enabled.clear(); renderAnimalList(); draw(); }});
document.getElementById("toggleStim").addEventListener("click", () => {{ showStim = !showStim; draw(); }});
document.getElementById("fullRange").addEventListener("click", () => {{
  document.getElementById("start").value = toLocalInput(dataMinX);
  document.getElementById("end").value = toLocalInput(dataMaxX);
  draw();
}});
document.querySelectorAll("[data-range-hours]").forEach(button => {{
  button.addEventListener("click", () => {{
    const hours = Number(button.dataset.rangeHours);
    document.getElementById("end").value = toLocalInput(dataMaxX);
    document.getElementById("start").value = toLocalInput(Math.max(dataMinX, dataMaxX - hours * 3600 * 1000));
    draw();
  }});
}});
window.addEventListener("resize", draw);
renderAnimalList();
draw();
</script>
</body>
</html>
"""


def _parse_local_datetime(value: str | None, local_tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    return _parse_timestamp(value, local_tz)


def _default_output_path(input_path: Path, output: Path | None) -> Path:
    if output:
        return output
    directory = input_path if input_path.is_dir() else input_path.parent
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return directory / f"temperature_snapshot_{stamp}.html"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plot temperature CSV data from session start through now minus a safety lag."
    )
    parser.add_argument("input", type=Path, help="Session folder or one active temperature CSV.")
    parser.add_argument("--output", type=Path, help="HTML output path. Defaults inside the session folder.")
    parser.add_argument("--config", type=Path, help="Optional run config for planned open-loop shading before session.yaml exists.")
    parser.add_argument("--local-timezone", help="Override local timezone. Defaults to session metadata or this computer's timezone.")
    parser.add_argument("--metadata", type=Path, help="Optional session.yaml path for trigger/stimulation windows.")
    parser.add_argument("--safe-lag-min", type=float, default=10.0, help="Exclude newest N minutes. Default: 10.")
    parser.add_argument("--since", help="Optional local start datetime, e.g. 2026-07-01T08:00:00.")
    parser.add_argument("--until", help="Optional local end datetime. Overrides now minus safety lag.")
    parser.add_argument("--max-points-per-animal", type=int, default=8000, help="Visual downsample cap per RFID. Default: 8000.")
    parser.add_argument("--no-open", action="store_true", help="Do not open the generated HTML in the default browser.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = args.input.resolve()
    metadata = _load_metadata(_metadata_path(input_path, args.metadata))
    config = _load_yaml_file(args.config.resolve() if args.config else None)
    local_tz = _resolve_local_timezone(args.local_timezone, metadata)
    now_local = datetime.now(timezone.utc).astimezone(local_tz)
    cutoff = _parse_local_datetime(args.until, local_tz) or (now_local - timedelta(minutes=args.safe_lag_min))
    since = _parse_local_datetime(args.since, local_tz)

    csv_paths = _discover_csvs(input_path)
    if not csv_paths:
        print(f"No CSV files found under {input_path}", file=sys.stderr)
        return 2

    readings = _load_readings(csv_paths, local_tz, cutoff)
    if since is not None:
        readings = [reading for reading in readings if reading.timestamp >= since]
    if not readings:
        print("No temperature readings matched the requested filters.", file=sys.stderr)
        return 1

    windows = _load_snapshot_windows(
        config=config,
        metadata=metadata,
        local_tz=local_tz,
        readings=readings,
        cutoff=cutoff,
    )
    if since is not None:
        windows = [window for window in windows if window.stop >= since]

    payload = _build_payload(readings, windows, args.max_points_per_animal)
    start = min(reading.timestamp for reading in readings)
    end = max(reading.timestamp for reading in readings)
    title = f"Temperature snapshot: {input_path.name}"
    subtitle = (
        f"{start.strftime('%Y-%m-%d %H:%M:%S %Z')} to {end.strftime('%Y-%m-%d %H:%M:%S %Z')} | "
        f"{len(readings):,} readings from {len(payload['animals'])} RFID(s) | cutoff {cutoff.strftime('%Y-%m-%d %H:%M:%S %Z')}"
    )
    output_path = _default_output_path(input_path, args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_html_document(payload, title, subtitle), encoding="utf-8")

    print(f"Wrote {output_path}")
    print(f"Read {len(csv_paths)} CSV file(s), plotted {len(readings):,} complete reading(s).")
    recorded = sum(1 for window in windows if window.source == "recorded")
    planned = sum(1 for window in windows if window.source == "planned")
    inferred = sum(1 for window in windows if window.source == "inferred")
    if recorded or planned or inferred:
        print(
            f"Loaded {recorded} recorded/commanded window(s), "
            f"{planned} planned window(s), and {inferred} inferred closed-loop window(s)."
        )
    else:
        print("No stimulation windows loaded or inferred.")
    if not args.no_open:
        webbrowser.open(output_path.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

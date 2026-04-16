from __future__ import annotations

import json
from typing import Any

import polars as pl
from zoneinfo import ZoneInfo

from .timebase import parse_local_naive_timestamp


TRIGGER_EVENT_SCHEMA = {
    "session_name": pl.Utf8,
    "animal_id": pl.Utf8,
    "animal_event_id": pl.Int64,
    "rule_id": pl.Utf8,
    "action": pl.Utf8,
    "stimulus_id": pl.Utf8,
    "reason": pl.Utf8,
    "channels": pl.List(pl.Utf8),
    "meta_json": pl.Utf8,
    "timestamp_local": pl.Datetime,
    "timestamp_utc": pl.Datetime,
}

STIMULATION_WINDOW_SCHEMA = {
    "session_name": pl.Utf8,
    "animal_id": pl.Utf8,
    "rule_id": pl.Utf8,
    "stimulus_id": pl.Utf8,
    "channel_name": pl.Utf8,
    "assignment_id": pl.Utf8,
    "assigned_animal_ids": pl.List(pl.Utf8),
    "start_reason": pl.Utf8,
    "stop_reason": pl.Utf8,
    "start_timestamp_local": pl.Datetime,
    "stop_timestamp_local": pl.Datetime,
    "start_timestamp_utc": pl.Datetime,
    "stop_timestamp_utc": pl.Datetime,
    "duration_seconds": pl.Float64,
}


def flatten_trigger_events(metadata: dict[str, Any], session_name: str, local_tz: ZoneInfo) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    grouped = metadata.get("triggers_by_animal", {})
    if not isinstance(grouped, dict):
        return pl.DataFrame(schema=TRIGGER_EVENT_SCHEMA)

    for animal_id, events in grouped.items():
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            meta = event.get("meta", {})
            if not isinstance(meta, dict):
                meta = {}
            local_dt, utc_dt = parse_local_naive_timestamp(str(event.get("timestamp", "")), local_tz)
            channels = [str(ch).strip() for ch in meta.get("channels", []) if str(ch).strip()]
            rows.append(
                {
                    "session_name": session_name,
                    "animal_id": str(animal_id),
                    "animal_event_id": int(event["animal_event_id"]) if event.get("animal_event_id") is not None else None,
                    "rule_id": str(event.get("rule_id") or ""),
                    "action": str(event.get("action") or ""),
                    "stimulus_id": str(event.get("stimulus_id") or ""),
                    "reason": str(event.get("reason") or ""),
                    "channels": channels,
                    "meta_json": json.dumps(meta, sort_keys=True),
                    "timestamp_local": local_dt,
                    "timestamp_utc": utc_dt,
                }
            )

    return pl.from_dicts(rows) if rows else pl.DataFrame(schema=TRIGGER_EVENT_SCHEMA)


def build_stimulation_windows(trigger_events: pl.DataFrame) -> pl.DataFrame:
    if trigger_events.is_empty():
        return pl.DataFrame(schema=STIMULATION_WINDOW_SCHEMA)

    rows = trigger_events.sort("timestamp_utc").to_dicts()
    active: dict[tuple[str, str, str], dict[str, object]] = {}
    windows: list[dict[str, object]] = []

    for row in rows:
        channels = row.get("channels") or [""]
        if not channels:
            channels = [""]
        action = str(row.get("action") or "").lower()
        for channel_name in channels:
            key = (
                str(row.get("animal_id") or ""),
                str(row.get("stimulus_id") or ""),
                str(channel_name or ""),
            )
            if action == "start":
                active[key] = row
            elif action == "stop" and key in active:
                start_row = active.pop(key)
                assignment = _assignment_for_channel(start_row, channel_name or "")
                start_utc = start_row["timestamp_utc"]
                stop_utc = row["timestamp_utc"]
                duration_seconds = (stop_utc - start_utc).total_seconds()
                windows.append(
                    {
                        "session_name": row["session_name"],
                        "animal_id": row["animal_id"],
                        "rule_id": row["rule_id"],
                        "stimulus_id": row["stimulus_id"],
                        "channel_name": channel_name or None,
                        "assignment_id": None if assignment is None else assignment.get("id"),
                        "assigned_animal_ids": [] if assignment is None else assignment.get("assigned_animal_ids", []),
                        "start_reason": start_row["reason"],
                        "stop_reason": row["reason"],
                        "start_timestamp_local": start_row["timestamp_local"],
                        "stop_timestamp_local": row["timestamp_local"],
                        "start_timestamp_utc": start_utc,
                        "stop_timestamp_utc": stop_utc,
                        "duration_seconds": duration_seconds,
                    }
                )

    return pl.from_dicts(windows) if windows else pl.DataFrame(schema=STIMULATION_WINDOW_SCHEMA)


def annotate_temperature_with_windows(
    temperature: pl.DataFrame,
    stimulation_windows: pl.DataFrame,
) -> pl.DataFrame:
    base_schema = dict(temperature.schema)
    annotated_schema = {
        **base_schema,
        "is_stim_active": pl.Boolean,
        "active_stimulus_id": pl.Utf8,
        "active_channel_name": pl.Utf8,
        "active_assignment_id": pl.Utf8,
    }
    if temperature.is_empty():
        return pl.DataFrame(schema=annotated_schema)
    if stimulation_windows.is_empty():
        return temperature.with_columns(
            pl.lit(False).alias("is_stim_active"),
            pl.lit(None, dtype=pl.Utf8).alias("active_stimulus_id"),
            pl.lit(None, dtype=pl.Utf8).alias("active_channel_name"),
            pl.lit(None, dtype=pl.Utf8).alias("active_assignment_id"),
        )

    global_windows = stimulation_windows.filter(pl.col("animal_id") == "__open_loop__").to_dicts()
    by_animal: dict[str, list[dict[str, object]]] = {}
    for row in stimulation_windows.filter(pl.col("animal_id") != "__open_loop__").to_dicts():
        by_animal.setdefault(str(row["animal_id"]), []).append(row)

    annotated: list[dict[str, object]] = []
    for row in temperature.sort("timestamp_utc").to_dicts():
        candidates = global_windows + by_animal.get(str(row["animal_id"]), [])
        match = next(
            (
                window
                for window in candidates
                if window["start_timestamp_utc"] <= row["timestamp_utc"] <= window["stop_timestamp_utc"]
            ),
            None,
        )
        row["is_stim_active"] = match is not None
        row["active_stimulus_id"] = None if match is None else match["stimulus_id"]
        row["active_channel_name"] = None if match is None else match["channel_name"]
        row["active_assignment_id"] = None if match is None else match.get("assignment_id")
        annotated.append(row)

    return pl.from_dicts(annotated, schema=annotated_schema)


def _assignment_for_channel(row: dict[str, Any], channel_name: str) -> dict[str, Any] | None:
    try:
        meta = json.loads(str(row.get("meta_json") or "{}"))
    except Exception:
        return None
    assignments = meta.get("assignments", [])
    if not isinstance(assignments, list):
        return None
    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        if str(assignment.get("channel") or "").strip() != channel_name:
            continue
        return {
            "id": str(assignment.get("id") or "").strip() or None,
            "assigned_animal_ids": [
                str(item).strip().upper()
                for item in assignment.get("assigned_animal_ids", [])
                if str(item).strip()
            ],
        }
    return None

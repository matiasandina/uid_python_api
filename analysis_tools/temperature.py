from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

import polars as pl
from zoneinfo import ZoneInfo

from .timebase import parse_local_naive_timestamp


def discover_temperature_csvs(session_dir: Path) -> list[Path]:
    return sorted(
        path for path in session_dir.glob("*.csv") if path.is_file() and not path.name.startswith(".")
    )


def _first_present(row: dict[str, str], keys: Iterable[str], default: str = "") -> str:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return str(row[key])
    return default


def load_temperature_table(session_dir: Path, session_name: str, local_tz: ZoneInfo) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for csv_path in discover_temperature_csvs(session_dir):
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row_index, raw_row in enumerate(reader, start=1):
                timestamp_text = _first_present(raw_row, ("DateTime", "Date"))
                if not timestamp_text:
                    continue
                local_dt, utc_dt = parse_local_naive_timestamp(timestamp_text, local_tz)
                uid = _first_present(raw_row, ("UID", "RFID")).strip().upper()
                temperature_raw = _first_present(raw_row, ("Temperature", "Temp"), default="")
                zone_raw = _first_present(raw_row, ("Zone",), default="")
                rows.append(
                    {
                        "session_name": session_name,
                        "device_name": csv_path.stem,
                        "source_file": csv_path.name,
                        "source_row": row_index,
                        "timestamp_local": local_dt,
                        "timestamp_utc": utc_dt,
                        "timestamp_utc_ns": int(utc_dt.timestamp() * 1_000_000_000),
                        "animal_id": uid,
                        "temperature_c": float(temperature_raw) if temperature_raw != "" else None,
                        "zone": int(zone_raw) if zone_raw != "" else None,
                    }
                )

    schema = {
        "session_name": pl.Utf8,
        "device_name": pl.Utf8,
        "source_file": pl.Utf8,
        "source_row": pl.Int64,
        "timestamp_local": pl.Datetime(time_zone=str(local_tz)),
        "timestamp_utc": pl.Datetime(time_zone="UTC"),
        "timestamp_utc_ns": pl.Int64,
        "animal_id": pl.Utf8,
        "temperature_c": pl.Float64,
        "zone": pl.Int64,
    }
    if not rows:
        return pl.DataFrame(schema=schema)

    table = pl.from_dicts(rows, schema=schema)
    return table.unique(subset=["animal_id", "timestamp_utc_ns"], keep="first", maintain_order=True)

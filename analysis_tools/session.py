from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import polars as pl
import yaml

from .temperature import load_temperature_table
from .timebase import parse_local_naive_timestamp, resolve_session_timezone
from .triggers import annotate_temperature_with_windows, build_stimulation_windows, flatten_trigger_events
from .ttl import build_ttl_pulses_table, build_ttl_qc_table, load_ttl_edges_table


@dataclass
class AnalysisSession:
    session_dir: Path
    session_name: str
    local_timezone: str
    session: pl.DataFrame
    temperature: pl.DataFrame
    trigger_events: pl.DataFrame
    stimulation_windows: pl.DataFrame
    ttl_edges: pl.DataFrame
    ttl_pulses: pl.DataFrame
    ttl_qc: pl.DataFrame
    temperature_annotated: pl.DataFrame

    def write_parquet(
        self,
        output_dir: str | Path | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> Path:
        progress = progress or _noop_progress
        target = Path(output_dir) if output_dir is not None else self.session_dir / "analysis"
        target.mkdir(parents=True, exist_ok=True)
        progress(f"Writing parquet tables into {target}")
        self.session.write_parquet(target / "session.parquet")
        progress(f"Wrote session.parquet ({self.session.height} row)")
        self.temperature.write_parquet(target / "temperature.parquet")
        progress(f"Wrote temperature.parquet ({self.temperature.height} rows)")
        self.trigger_events.write_parquet(target / "trigger_events.parquet")
        progress(f"Wrote trigger_events.parquet ({self.trigger_events.height} rows)")
        self.stimulation_windows.write_parquet(target / "stimulation_windows.parquet")
        progress(f"Wrote stimulation_windows.parquet ({self.stimulation_windows.height} rows)")
        self.ttl_edges.write_parquet(target / "ttl_edges.parquet")
        progress(f"Wrote ttl_edges.parquet ({self.ttl_edges.height} rows)")
        self.ttl_pulses.write_parquet(target / "ttl_pulses.parquet")
        progress(f"Wrote ttl_pulses.parquet ({self.ttl_pulses.height} rows)")
        self.ttl_qc.write_parquet(target / "ttl_qc.parquet")
        progress(f"Wrote ttl_qc.parquet ({self.ttl_qc.height} rows)")
        self.temperature_annotated.write_parquet(target / "temperature_annotated.parquet")
        progress(f"Wrote temperature_annotated.parquet ({self.temperature_annotated.height} rows)")
        return target


def load_analysis_session(
    session_dir: str | Path,
    local_timezone: str | None = None,
    ttl_active_low: bool = True,
    progress: Callable[[str], None] | None = None,
) -> AnalysisSession:
    progress = progress or _noop_progress
    root = Path(session_dir)
    progress(f"Loading session from {root}")
    metadata_path = root / "session.yaml"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Missing session metadata file: {metadata_path}")

    progress("Reading session metadata")
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
    if not isinstance(metadata, dict):
        raise ValueError("Session metadata must be a YAML mapping.")

    session_name = root.name
    tz = resolve_session_timezone(metadata, override=local_timezone)
    progress(f"Resolved local timezone: {tz}")

    session_table = _build_session_table(metadata, session_name, str(tz))
    progress("Loading and merging temperature CSV files")
    temperature = load_temperature_table(root, session_name=session_name, local_tz=tz)
    progress(f"Loaded {temperature.height} temperature rows after deduplication")
    progress("Flattening trigger events and stimulation windows")
    trigger_events = flatten_trigger_events(metadata, session_name=session_name, local_tz=tz)
    stimulation_windows = build_stimulation_windows(trigger_events)
    progress(
        f"Loaded {trigger_events.height} trigger events and {stimulation_windows.height} stimulation windows"
    )
    progress("Decoding TTL raw into edge table")
    ttl_edges = load_ttl_edges_table(
        root,
        session_name=session_name,
        progress=progress,
        active_low=ttl_active_low,
    )
    progress(f"Decoded {ttl_edges.height} TTL edges")
    progress("Building TTL pulse table")
    ttl_pulses = build_ttl_pulses_table(ttl_edges)
    progress(f"Built {ttl_pulses.height} TTL pulses")
    ttl_qc = build_ttl_qc_table(
        ttl_edges,
        ttl_pulses,
        stimulus_config=metadata.get("config", {}).get("stimulus", {}),
    )
    progress("Built TTL QC summary")
    progress("Annotating temperature rows with stimulation windows")
    temperature_annotated = annotate_temperature_with_windows(temperature, stimulation_windows)
    progress("Session analysis tables are ready")

    return AnalysisSession(
        session_dir=root,
        session_name=session_name,
        local_timezone=str(tz),
        session=session_table,
        temperature=temperature,
        trigger_events=trigger_events,
        stimulation_windows=stimulation_windows,
        ttl_edges=ttl_edges,
        ttl_pulses=ttl_pulses,
        ttl_qc=ttl_qc,
        temperature_annotated=temperature_annotated,
    )


def _build_session_table(metadata: dict[str, Any], session_name: str, local_timezone: str) -> pl.DataFrame:
    session = metadata.get("session", {})
    config = metadata.get("config", {})
    start_local, start_utc = parse_local_naive_timestamp(str(session.get("start_time", "")), resolve_session_timezone(metadata, local_timezone))
    end_local, end_utc = parse_local_naive_timestamp(str(session.get("end_time", "")), resolve_session_timezone(metadata, local_timezone))

    row = {
        "session_name": session_name,
        "session_label": session.get("session_label"),
        "protocol_name": session.get("protocol_name"),
        "mode": session.get("mode"),
        "source": session.get("source"),
        "local_timezone": local_timezone,
        "start_time_local": start_local,
        "start_time_utc": start_utc,
        "end_time_local": end_local,
        "end_time_utc": end_utc,
        "duration_seconds": (end_utc - start_utc).total_seconds(),
        "stimulus_enabled": bool((config.get("stimulus") or {}).get("enabled", False)) if isinstance(config, dict) else False,
        "ttl_capture_enabled": bool((config.get("ttl_capture") or {}).get("enabled", False)) if isinstance(config, dict) else False,
    }
    return pl.from_dicts([row])


def _noop_progress(_: str) -> None:
    return None

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import polars as pl
from ttl_capture.frame_index import read_frame_index


TTL_EDGE_SCHEMA = {
    "session_name": pl.Utf8,
    "channel_name": pl.Utf8,
    "edge_type": pl.Utf8,
    "sample_index": pl.Int64,
    "timestamp_monotonic_ns": pl.Int64,
    "timestamp_estimated_utc": pl.Datetime(time_zone="UTC"),
    "pulse_width_ms": pl.Float64,
}

TTL_PULSE_SCHEMA = {
    "session_name": pl.Utf8,
    "channel_name": pl.Utf8,
    "start_sample_index": pl.Int64,
    "stop_sample_index": pl.Int64,
    "start_timestamp_monotonic_ns": pl.Int64,
    "stop_timestamp_monotonic_ns": pl.Int64,
    "start_timestamp_estimated_utc": pl.Datetime(time_zone="UTC"),
    "stop_timestamp_estimated_utc": pl.Datetime(time_zone="UTC"),
    "pulse_width_ms": pl.Float64,
}

TTL_QC_SCHEMA = {
    "channel_name": pl.Utf8,
    "expected_active": pl.Boolean,
    "startup_singleton_artifact": pl.Boolean,
    "rising_edges": pl.Int64,
    "falling_edges": pl.Int64,
    "pulse_count": pl.Int64,
    "observed_frequency_hz": pl.Float64,
    "observed_effective_frequency_hz": pl.Float64,
    "observed_pulse_width_ms": pl.Float64,
    "expected_period_ms": pl.Float64,
    "expected_frequency_hz": pl.Float64,
    "expected_pulse_width_ms": pl.Float64,
    "frequency_ok": pl.Boolean,
    "pulse_width_ok": pl.Boolean,
    "note": pl.Utf8,
}


def load_ttl_edges_table(
    session_dir: Path,
    session_name: str,
    progress: Callable[[str], None] | None = None,
    active_low: bool = True,
) -> pl.DataFrame:
    progress = progress or _noop_progress
    ttl_meta_path = session_dir / "ttl_meta.json"
    ttl_raw_path = session_dir / "ttl_raw.bin"
    ttl_frames_path = session_dir / "ttl_frames.bin"
    if not ttl_meta_path.exists() or not ttl_raw_path.exists():
        return pl.DataFrame(schema=TTL_EDGE_SCHEMA)

    ttl_meta: dict[str, Any] = json.loads(ttl_meta_path.read_text(encoding="utf-8"))
    raw_size_bytes = ttl_raw_path.stat().st_size
    frame_size = int(ttl_meta["frame_size"])
    sample_rate_hz = int(ttl_meta["sampling_rate_hz"])
    t0_monotonic_ns = int(ttl_meta.get("t0_monotonic_ns", 0) or 0)
    t0_frame_id = int(ttl_meta.get("t0_frame_id", 0) or 0)
    channel_map = [int(v) for v in ttl_meta.get("channel_map", [1, 2, 3, 4])]
    channels = min(4, len(channel_map))
    wall_clock_start = _parse_wall_clock_start(ttl_meta)

    progress(f"Reading TTL raw into memory ({raw_size_bytes / (1024 ** 3):.2f} GiB)")
    raw = np.fromfile(ttl_raw_path, dtype=np.uint8)
    if raw.size == 0:
        return pl.DataFrame(schema=TTL_EDGE_SCHEMA)

    if ttl_frames_path.exists():
        progress("Reading TTL frame index sidecar")
        header, records = read_frame_index(ttl_frames_path)
        if header.frame_size != frame_size:
            raise ValueError(
                f"TTL frame index frame_size mismatch: meta={frame_size} index={header.frame_size}"
            )
        if header.sampling_rate_hz != sample_rate_hz:
            raise ValueError(
                f"TTL frame index sampling_rate_hz mismatch: meta={sample_rate_hz} index={header.sampling_rate_hz}"
            )
        payload_offsets = np.array([record.payload_offset_bytes for record in records], dtype=np.int64)
        frame_ids = np.array([record.frame_id for record in records], dtype=np.int64)
        raw = _trim_or_validate_indexed_raw(raw=raw, frame_size=frame_size, payload_offsets=payload_offsets)
        progress(
            f"Vectorized TTL decode over {raw.size:,} samples across {len(records)} indexed frame(s) "
            f"(active_low={active_low})"
        )
    else:
        usable_samples = (raw.size // frame_size) * frame_size
        if usable_samples == 0:
            return pl.DataFrame(schema=TTL_EDGE_SCHEMA)
        if usable_samples != raw.size:
            raw = raw[:usable_samples]
        payload_offsets = np.arange(raw.size // frame_size, dtype=np.int64) * frame_size
        frame_ids = t0_frame_id + np.arange(raw.size // frame_size, dtype=np.int64)
        progress(
            f"Vectorized TTL decode over {raw.size:,} samples "
            f"(active_low={active_low})"
        )

    rows = _decode_edge_rows(
        raw=raw,
        session_name=session_name,
        frame_ids=frame_ids,
        payload_offsets=payload_offsets,
        frame_size=frame_size,
        sample_rate_hz=sample_rate_hz,
        t0_monotonic_ns=t0_monotonic_ns,
        t0_frame_id=t0_frame_id,
        wall_clock_start=wall_clock_start,
        channel_map=channel_map,
        channels=channels,
        active_low=active_low,
        progress=progress,
    )

    return pl.from_dicts(rows, schema=TTL_EDGE_SCHEMA) if rows else pl.DataFrame(schema=TTL_EDGE_SCHEMA)


def build_ttl_pulses_table(ttl_edges: pl.DataFrame) -> pl.DataFrame:
    if ttl_edges.is_empty():
        return pl.DataFrame(schema=TTL_PULSE_SCHEMA)

    pulses: list[dict[str, object]] = []
    active_by_channel: dict[str, dict[str, object]] = {}
    for row in ttl_edges.sort(["channel_name", "sample_index"]).to_dicts():
        channel_name = str(row["channel_name"])
        edge_type = str(row["edge_type"])
        if edge_type == "rising":
            active_by_channel[channel_name] = row
            continue
        if edge_type != "falling" or channel_name not in active_by_channel:
            continue
        start = active_by_channel.pop(channel_name)
        pulses.append(
            {
                "session_name": row["session_name"],
                "channel_name": channel_name,
                "start_sample_index": start["sample_index"],
                "stop_sample_index": row["sample_index"],
                "start_timestamp_monotonic_ns": start["timestamp_monotonic_ns"],
                "stop_timestamp_monotonic_ns": row["timestamp_monotonic_ns"],
                "start_timestamp_estimated_utc": start["timestamp_estimated_utc"],
                "stop_timestamp_estimated_utc": row["timestamp_estimated_utc"],
                "pulse_width_ms": row["pulse_width_ms"],
            }
        )

    return pl.from_dicts(pulses, schema=TTL_PULSE_SCHEMA) if pulses else pl.DataFrame(schema=TTL_PULSE_SCHEMA)


def reconstruct_sample_masks_from_pulses(
    ttl_pulses: pl.DataFrame,
    *,
    total_samples: int | None = None,
) -> bytes:
    if ttl_pulses.is_empty():
        return bytes(total_samples or 0)

    pulse_rows = ttl_pulses.to_dicts()
    if total_samples is None:
        total_samples = max(int(row["stop_sample_index"]) for row in pulse_rows)

    samples = bytearray(int(total_samples))
    for row in pulse_rows:
        bit_mask = _channel_name_to_bitmask(str(row["channel_name"]))
        start = max(0, int(row["start_sample_index"]))
        stop = min(int(total_samples), int(row["stop_sample_index"]))
        for index in range(start, stop):
            samples[index] |= bit_mask
    return bytes(samples)


def build_ttl_qc_table(
    ttl_edges: pl.DataFrame,
    ttl_pulses: pl.DataFrame,
    *,
    stimulus_config: dict[str, Any] | None = None,
    freq_tolerance_frac: float = 0.05,
    pulse_width_tolerance_ms: float = 1.0,
) -> pl.DataFrame:
    stimulus_config = stimulus_config or {}
    channels_cfg = stimulus_config.get("channels", {}) if isinstance(stimulus_config, dict) else {}
    configured_channels = sorted(str(name).strip() for name in channels_cfg.keys() if str(name).strip())
    seen_channels = set(ttl_edges.get_column("channel_name").to_list()) if not ttl_edges.is_empty() else set()
    all_channels = sorted(seen_channels.union(configured_channels))

    pulse_cfg = stimulus_config.get("pulse", {}) if isinstance(stimulus_config, dict) else {}
    expected_period_ms = _safe_float(pulse_cfg.get("period_ms"))
    expected_pulse_width_ms = _safe_float(pulse_cfg.get("time_on_ms"))
    expected_frequency_hz = None if not expected_period_ms else 1000.0 / expected_period_ms
    target_channels = {
        str(ch).strip()
        for ch in stimulus_config.get("target_channels", [])
        if str(ch).strip()
    } if isinstance(stimulus_config, dict) else set()

    rows: list[dict[str, object]] = []
    for channel_name in all_channels:
        channel_edges = ttl_edges.filter(pl.col("channel_name") == channel_name) if not ttl_edges.is_empty() else pl.DataFrame(schema=TTL_EDGE_SCHEMA)
        channel_pulses = ttl_pulses.filter(pl.col("channel_name") == channel_name) if not ttl_pulses.is_empty() else pl.DataFrame(schema=TTL_PULSE_SCHEMA)

        edge_types = channel_edges.get_column("edge_type").to_list() if not channel_edges.is_empty() else []
        rising_edges = sum(1 for edge_type in edge_types if edge_type == "rising")
        falling_edges = sum(1 for edge_type in edge_types if edge_type == "falling")
        pulse_count = channel_pulses.height
        observed_frequency_hz = _infer_frequency_hz(
            channel_pulses,
            expected_period_ms=expected_period_ms,
        )
        observed_effective_frequency_hz = _infer_effective_frequency_hz(channel_pulses)
        observed_pulse_width_ms = None if channel_pulses.is_empty() else float(channel_pulses.get_column("pulse_width_ms").mean())

        startup_singleton_artifact = rising_edges == 1 and falling_edges == 0 and pulse_count == 0
        expected_active = channel_name in target_channels
        frequency_ok = _within_fraction(observed_frequency_hz, expected_frequency_hz, freq_tolerance_frac) if expected_active else None
        pulse_width_ok = _within_abs(observed_pulse_width_ms, expected_pulse_width_ms, pulse_width_tolerance_ms) if expected_active else None

        rows.append(
            {
                "channel_name": channel_name,
                "expected_active": expected_active,
                "startup_singleton_artifact": startup_singleton_artifact,
                "rising_edges": rising_edges,
                "falling_edges": falling_edges,
                "pulse_count": pulse_count,
                "observed_frequency_hz": observed_frequency_hz,
                "observed_effective_frequency_hz": observed_effective_frequency_hz,
                "observed_pulse_width_ms": observed_pulse_width_ms,
                "expected_period_ms": expected_period_ms,
                "expected_frequency_hz": expected_frequency_hz,
                "expected_pulse_width_ms": expected_pulse_width_ms,
                "frequency_ok": frequency_ok,
                "pulse_width_ok": pulse_width_ok,
                "note": _build_qc_note(
                    expected_active=expected_active,
                    startup_singleton_artifact=startup_singleton_artifact,
                    pulse_count=pulse_count,
                    frequency_ok=frequency_ok,
                    pulse_width_ok=pulse_width_ok,
                ),
            }
        )

    return pl.from_dicts(rows, schema=TTL_QC_SCHEMA) if rows else pl.DataFrame(schema=TTL_QC_SCHEMA)


def _edge_row(
    *,
    session_name: str,
    channel_name: str,
    edge_type: str,
    sample_index: int,
    pulse_width_ms: float,
    sample_rate_hz: int,
    t0_monotonic_ns: int,
    t0_frame_id: int,
    frame_size: int,
    wall_clock_start: datetime | None,
) -> dict[str, object]:
    base_sample_index = int(t0_frame_id) * int(frame_size)
    delta_samples = int(sample_index) - base_sample_index
    monotonic_ns = t0_monotonic_ns + int((delta_samples * 1_000_000_000) / sample_rate_hz)
    estimated_utc = None
    if wall_clock_start is not None and t0_monotonic_ns > 0:
        estimated_utc = wall_clock_start + timedelta(microseconds=delta_samples * 1_000_000 / sample_rate_hz)
    return {
        "session_name": session_name,
        "channel_name": channel_name,
        "edge_type": edge_type,
        "sample_index": sample_index,
        "timestamp_monotonic_ns": monotonic_ns,
        "timestamp_estimated_utc": estimated_utc,
        "pulse_width_ms": pulse_width_ms,
    }


def _parse_wall_clock_start(ttl_meta: dict[str, Any]) -> datetime | None:
    value = ttl_meta.get("wall_clock_start_iso")
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _trim_or_validate_indexed_raw(
    *,
    raw: np.ndarray,
    frame_size: int,
    payload_offsets: np.ndarray,
) -> np.ndarray:
    if payload_offsets.size == 0:
        return np.empty(0, dtype=np.uint8)
    required_bytes = int(payload_offsets[-1]) + int(frame_size)
    if raw.size < required_bytes:
        raise ValueError(
            f"TTL raw payload is truncated for indexed decode: need {required_bytes} bytes, found {raw.size}."
        )
    if raw.size > required_bytes:
        raw = raw[:required_bytes]
    return raw


def _decode_edge_rows(
    *,
    raw: np.ndarray,
    session_name: str,
    frame_ids: np.ndarray,
    payload_offsets: np.ndarray,
    frame_size: int,
    sample_rate_hz: int,
    t0_monotonic_ns: int,
    t0_frame_id: int,
    wall_clock_start: datetime | None,
    channel_map: list[int],
    channels: int,
    active_low: bool,
    progress: Callable[[str], None],
) -> list[dict[str, object]]:
    if frame_ids.size == 0:
        return []
    frame_payloads = _reshape_or_gather_frame_payloads(
        raw=raw,
        frame_size=frame_size,
        payload_offsets=payload_offsets,
    )
    contiguous = np.zeros(frame_ids.size, dtype=bool)
    if frame_ids.size > 1:
        contiguous[1:] = frame_ids[1:] == (frame_ids[:-1] + 1)

    rows: list[dict[str, object]] = []
    for ch in range(channels):
        channel_name = f"ch{channel_map[ch]}"
        states = ((frame_payloads >> ch) & 0x01).astype(np.int8, copy=False)
        if active_low:
            states = 1 - states

        prev = np.empty_like(states)
        prev[:, 1:] = states[:, :-1]
        prev[0, 0] = 0
        if frame_ids.size > 1:
            prev[1:, 0] = np.where(contiguous[1:], states[:-1, -1], 0)
        transitions = states - prev

        rising_pos = np.argwhere(transitions == 1)
        falling_pos = np.argwhere(transitions == -1)
        rising = (frame_ids[rising_pos[:, 0]] * frame_size + rising_pos[:, 1]).astype(np.int64, copy=False)
        falling = (frame_ids[falling_pos[:, 0]] * frame_size + falling_pos[:, 1]).astype(np.int64, copy=False)

        progress(
            f"{channel_name}: {rising.size} rising edges, {falling.size} falling edges"
        )

        fall_ptr = 0
        n_falls = falling.size
        channel_row_start = len(rows)

        for sample_index in rising.tolist():
            rows.append(
                _edge_row(
                    session_name=session_name,
                    channel_name=channel_name,
                    edge_type="rising",
                    sample_index=int(sample_index),
                    pulse_width_ms=0.0,
                    sample_rate_hz=sample_rate_hz,
                    t0_monotonic_ns=t0_monotonic_ns,
                    t0_frame_id=t0_frame_id,
                    frame_size=frame_size,
                    wall_clock_start=wall_clock_start,
                )
            )

        for sample_index in falling.tolist():
            while fall_ptr < n_falls and falling[fall_ptr] < sample_index:
                fall_ptr += 1
            rows.append(
                _edge_row(
                    session_name=session_name,
                    channel_name=channel_name,
                    edge_type="falling",
                    sample_index=int(sample_index),
                    pulse_width_ms=0.0,
                    sample_rate_hz=sample_rate_hz,
                    t0_monotonic_ns=t0_monotonic_ns,
                    t0_frame_id=t0_frame_id,
                    frame_size=frame_size,
                    wall_clock_start=wall_clock_start,
                )
            )

        if rising.size and falling.size:
            paired_fall_indices = np.searchsorted(falling, rising, side="right")
            pulse_width_by_fall: dict[int, float] = {}
            for rise_sample, fall_pos in zip(rising.tolist(), paired_fall_indices.tolist()):
                if fall_pos >= n_falls:
                    continue
                fall_sample = int(falling[fall_pos])
                width_ms = ((fall_sample - int(rise_sample)) / float(sample_rate_hz)) * 1000.0
                pulse_width_by_fall[fall_sample] = width_ms
            for row in rows[channel_row_start:]:
                if row["channel_name"] == channel_name and row["edge_type"] == "falling":
                    sample_index = int(row["sample_index"])
                    if sample_index in pulse_width_by_fall:
                        row["pulse_width_ms"] = pulse_width_by_fall[sample_index]

    rows.sort(key=lambda item: (str(item["channel_name"]), int(item["sample_index"]), str(item["edge_type"])))
    return rows


def _reshape_or_gather_frame_payloads(
    *,
    raw: np.ndarray,
    frame_size: int,
    payload_offsets: np.ndarray,
) -> np.ndarray:
    if payload_offsets.size == 0:
        return np.empty((0, frame_size), dtype=np.uint8)
    expected_offsets = np.arange(payload_offsets.size, dtype=np.int64) * frame_size
    expected_bytes = payload_offsets.size * frame_size
    if raw.size == expected_bytes and np.array_equal(payload_offsets, expected_offsets):
        return raw.reshape(payload_offsets.size, frame_size)

    frame_payloads = np.empty((payload_offsets.size, frame_size), dtype=np.uint8)
    for idx, payload_offset in enumerate(payload_offsets.tolist()):
        start = int(payload_offset)
        stop = start + int(frame_size)
        frame_payloads[idx, :] = raw[start:stop]
    return frame_payloads


def _channel_name_to_bitmask(channel_name: str) -> int:
    match = re.fullmatch(r"ch(\d+)", channel_name.strip().lower())
    if not match:
        raise ValueError(f"Unsupported channel name for reconstruction: {channel_name}")
    channel_number = int(match.group(1))
    if channel_number <= 0:
        raise ValueError(f"Channel number must be > 0: {channel_name}")
    return 1 << (channel_number - 1)


def _noop_progress(_: str) -> None:
    return None


def _infer_frequency_hz(
    channel_pulses: pl.DataFrame,
    *,
    expected_period_ms: float | None = None,
) -> float | None:
    intervals_ns = _pulse_start_intervals_ns(channel_pulses)
    if not intervals_ns:
        return None
    representative_interval_ns = _infer_representative_interval_ns(
        intervals_ns,
        expected_period_ms=expected_period_ms,
    )
    if representative_interval_ns is None or representative_interval_ns <= 0:
        return None
    return 1e9 / representative_interval_ns


def _infer_effective_frequency_hz(channel_pulses: pl.DataFrame) -> float | None:
    intervals_ns = _pulse_start_intervals_ns(channel_pulses)
    if not intervals_ns:
        return None
    avg_interval_ns = sum(intervals_ns) / len(intervals_ns)
    if avg_interval_ns <= 0:
        return None
    return 1e9 / avg_interval_ns


def _pulse_start_intervals_ns(channel_pulses: pl.DataFrame) -> list[int]:
    if channel_pulses.height < 2:
        return []
    starts_ns = [int(v) for v in channel_pulses.get_column("start_timestamp_monotonic_ns").to_list()]
    return [
        curr - prev
        for prev, curr in zip(starts_ns, starts_ns[1:])
        if curr > prev
    ]


def _infer_representative_interval_ns(
    intervals_ns: list[int],
    *,
    expected_period_ms: float | None = None,
) -> float | None:
    if not intervals_ns:
        return None

    if expected_period_ms is not None and expected_period_ms > 0:
        expected_interval_ns = expected_period_ms * 1_000_000.0
        matched = [
            interval_ns
            for interval_ns in intervals_ns
            if abs(interval_ns - expected_interval_ns) / expected_interval_ns <= 0.25
        ]
        if matched:
            return float(np.median(np.array(matched, dtype=np.float64)))

    min_interval_ns = min(intervals_ns)
    fast_cluster_limit_ns = min_interval_ns * 1.5
    fast_cluster = [
        interval_ns
        for interval_ns in intervals_ns
        if interval_ns <= fast_cluster_limit_ns
    ]
    if fast_cluster:
        return float(np.median(np.array(fast_cluster, dtype=np.float64)))

    return float(np.median(np.array(intervals_ns, dtype=np.float64)))


def _within_fraction(observed: float | None, expected: float | None, tolerance_frac: float) -> bool | None:
    if observed is None or expected is None or expected == 0:
        return None
    return abs(observed - expected) / expected <= tolerance_frac


def _within_abs(observed: float | None, expected: float | None, tolerance_abs: float) -> bool | None:
    if observed is None or expected is None:
        return None
    return abs(observed - expected) <= tolerance_abs


def _build_qc_note(
    *,
    expected_active: bool,
    startup_singleton_artifact: bool,
    pulse_count: int,
    frequency_ok: bool | None,
    pulse_width_ok: bool | None,
) -> str:
    if startup_singleton_artifact:
        return "startup singleton edge on otherwise inactive channel"
    if expected_active and pulse_count == 0:
        return "expected active but no pulses found"
    if expected_active and (frequency_ok is False or pulse_width_ok is False):
        return "observed TTL differs from pulse spec"
    if expected_active:
        return "active channel within tolerance" if frequency_ok and pulse_width_ok else "active channel"
    if pulse_count > 0:
        return "unexpected pulses on non-target channel"
    return "inactive channel"


def _safe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except Exception:
        return None

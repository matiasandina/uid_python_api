from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Dict, Iterable, List, Optional

import yaml
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .capture import reconstruct_timestamp_ns
from .edges import TTLEdge, extract_edges_from_payload
from .frame_index import TTLFrameIndexRecord, read_frame_index

ProgressReporter = Optional[Callable[[str], None]]


@dataclass(frozen=True)
class CommandWindow:
    channel_name: str
    start_monotonic_ns: int
    stop_monotonic_ns: int
    start_timestamp: str
    stop_timestamp: str
    start_reason: str
    stop_reason: str


@dataclass(frozen=True)
class WindowVerification:
    channel_name: str
    start_monotonic_ns: int
    stop_monotonic_ns: int
    pulse_count: int
    first_pulse_latency_ms: Optional[float]
    last_pulse_before_stop_ms: Optional[float]
    inferred_frequency_hz: Optional[float]
    ok: bool
    note: str


@dataclass(frozen=True)
class FrameContinuitySummary:
    frame_index_present: bool
    first_frame_id: Optional[int]
    last_frame_id: Optional[int]
    frame_records: int
    missing_frame_count: int
    gap_ranges: List[str]


@dataclass(frozen=True)
class SampleCoverageEstimate:
    total_samples: int
    saved_duration_seconds: Optional[float]
    estimated_frame_count_from_raw: Optional[int]
    trailing_partial_frame_bytes: int
    expected_session_duration_seconds: Optional[float]
    expected_session_samples: Optional[int]
    session_sample_coverage_ratio: Optional[float]
    note: str


@dataclass(frozen=True)
class SessionVerificationReport:
    session_name: str
    ttl_enabled: bool
    metadata_path: Path
    ttl_meta_path: Optional[Path]
    ttl_raw_path: Optional[Path]
    continuity: FrameContinuitySummary
    sample_coverage: SampleCoverageEstimate
    total_rising_edges: int
    stray_rising_edges: int
    windows_verified: int
    windows_ok: int
    frequency_note: Optional[str]
    issues: List[str]
    channel_summaries: Dict[str, Dict[str, Any]]
    window_results: List[WindowVerification]


def verify_session(
    metadata_path: str | Path,
    tolerance_ms: float = 100.0,
    progress: ProgressReporter = None,
) -> SessionVerificationReport:
    metadata_file = Path(metadata_path)
    _emit_progress(progress, f"Loading session metadata from `{metadata_file}`")
    payload = yaml.safe_load(metadata_file.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("Session metadata must contain a YAML mapping.")

    session_folder = metadata_file.parent
    session_name = session_folder.name
    ttl_meta_path = session_folder / "ttl_meta.json"
    ttl_raw_path = session_folder / "ttl_raw.bin"
    issues: List[str] = []

    if not ttl_meta_path.exists() or not ttl_raw_path.exists():
        if not ttl_meta_path.exists():
            issues.append(f"Missing TTL metadata file: {ttl_meta_path.name}")
        if not ttl_raw_path.exists():
            issues.append(f"Missing TTL raw file: {ttl_raw_path.name}")
        return SessionVerificationReport(
            session_name=session_name,
            ttl_enabled=False,
            metadata_path=metadata_file,
            ttl_meta_path=ttl_meta_path if ttl_meta_path.exists() else None,
            ttl_raw_path=ttl_raw_path if ttl_raw_path.exists() else None,
            continuity=FrameContinuitySummary(
                frame_index_present=False,
                first_frame_id=None,
                last_frame_id=None,
                frame_records=0,
                missing_frame_count=0,
                gap_ranges=[],
            ),
            sample_coverage=SampleCoverageEstimate(
                total_samples=0,
                saved_duration_seconds=None,
                estimated_frame_count_from_raw=None,
                trailing_partial_frame_bytes=0,
                expected_session_duration_seconds=None,
                expected_session_samples=None,
                session_sample_coverage_ratio=None,
                note="TTL artifacts are incomplete, so sample coverage could not be estimated.",
            ),
            total_rising_edges=0,
            stray_rising_edges=0,
            windows_verified=0,
            windows_ok=0,
            frequency_note=None,
            issues=issues,
            channel_summaries={},
            window_results=[],
        )

    _emit_progress(progress, "Reading TTL metadata and estimating raw sample coverage")
    ttl_meta = json.loads(ttl_meta_path.read_text(encoding="utf-8"))
    continuity = summarize_frame_continuity(ttl_raw_path=ttl_raw_path)
    sample_coverage = estimate_sample_coverage(
        metadata_payload=payload,
        ttl_raw_path=ttl_raw_path,
        ttl_meta=ttl_meta,
    )
    _emit_progress(progress, "Beginning TTL edge reconstruction; this may take a while for long sessions")
    edges = load_ttl_edges(ttl_raw_path=ttl_raw_path, ttl_meta=ttl_meta, progress=progress)
    rising_edges = [edge for edge in edges if edge["edge"].edge_type == "rising"]
    _emit_progress(progress, f"Reconstructed {len(edges)} TTL edges ({len(rising_edges)} rising); matching command windows")
    windows = extract_command_windows(payload)
    frequency_note = build_frequency_note(payload)

    tolerance_ns = int(max(0.0, tolerance_ms) * 1_000_000.0)
    window_results: List[WindowVerification] = []
    used_edge_ids: set[int] = set()

    for window in windows:
        window_rising = [
            edge
            for edge in rising_edges
            if (window.start_monotonic_ns - tolerance_ns) <= edge["monotonic_ns"] <= (window.stop_monotonic_ns + tolerance_ns)
            and edge["channel_name"] == window.channel_name
        ]
        for edge in window_rising:
            used_edge_ids.add(id(edge))
        window_results.append(_verify_window(window, window_rising))

    stray_rising_edges = [
        edge for edge in rising_edges if id(edge) not in used_edge_ids
    ]
    if not windows:
        issues.append("No command windows with monotonic timing were found in session metadata.")
    if stray_rising_edges:
        issues.append(f"Detected {len(stray_rising_edges)} rising TTL edge(s) outside commanded windows.")
    if continuity.frame_index_present and continuity.missing_frame_count > 0:
        issues.append(
            f"Detected {continuity.missing_frame_count} missing TTL frame(s) from `ttl_frames.bin`: "
            + ", ".join(continuity.gap_ranges)
        )

    channel_summaries = summarize_edges_by_channel(rising_edges)
    windows_ok = sum(1 for result in window_results if result.ok)

    return SessionVerificationReport(
        session_name=session_name,
        ttl_enabled=True,
        metadata_path=metadata_file,
        ttl_meta_path=ttl_meta_path,
        ttl_raw_path=ttl_raw_path,
        continuity=continuity,
        sample_coverage=sample_coverage,
        total_rising_edges=len(rising_edges),
        stray_rising_edges=len(stray_rising_edges),
        windows_verified=len(window_results),
        windows_ok=windows_ok,
        frequency_note=frequency_note,
        issues=issues,
        channel_summaries=channel_summaries,
        window_results=window_results,
    )


def summarize_frame_continuity(ttl_raw_path: str | Path) -> FrameContinuitySummary:
    frames_path = Path(ttl_raw_path).with_name("ttl_frames.bin")
    if not frames_path.exists():
        return FrameContinuitySummary(
            frame_index_present=False,
            first_frame_id=None,
            last_frame_id=None,
            frame_records=0,
            missing_frame_count=0,
            gap_ranges=[],
        )

    _header, records = read_frame_index(frames_path)
    if not records:
        return FrameContinuitySummary(
            frame_index_present=True,
            first_frame_id=None,
            last_frame_id=None,
            frame_records=0,
            missing_frame_count=0,
            gap_ranges=[],
        )

    gap_ranges, missing_frame_count = _frame_gap_summary(records)
    return FrameContinuitySummary(
        frame_index_present=True,
        first_frame_id=int(records[0].frame_id),
        last_frame_id=int(records[-1].frame_id),
        frame_records=len(records),
        missing_frame_count=missing_frame_count,
        gap_ranges=gap_ranges,
    )


def estimate_sample_coverage(
    *,
    metadata_payload: Dict[str, Any],
    ttl_raw_path: str | Path,
    ttl_meta: Dict[str, Any],
) -> SampleCoverageEstimate:
    raw_size_bytes = Path(ttl_raw_path).stat().st_size
    total_samples = int(raw_size_bytes)
    sample_rate_hz = int(ttl_meta["sampling_rate_hz"])
    frame_size = int(ttl_meta["frame_size"])
    saved_duration_seconds = (total_samples / float(sample_rate_hz)) if sample_rate_hz > 0 else None
    estimated_frame_count_from_raw = (total_samples // frame_size) if frame_size > 0 else None
    trailing_partial_frame_bytes = total_samples % frame_size if frame_size > 0 else 0

    expected_session_duration_seconds: Optional[float] = None
    expected_session_samples: Optional[int] = None
    session_sample_coverage_ratio: Optional[float] = None
    session_payload = metadata_payload.get("session", {})
    if isinstance(session_payload, dict):
        start_time = _parse_iso_datetime(session_payload.get("start_time"))
        end_time = _parse_iso_datetime(session_payload.get("end_time"))
        if start_time is not None and end_time is not None:
            expected_session_duration_seconds = max(0.0, (end_time - start_time).total_seconds())
            expected_session_samples = int(round(expected_session_duration_seconds * sample_rate_hz))
            if expected_session_samples > 0:
                session_sample_coverage_ratio = total_samples / float(expected_session_samples)

    if expected_session_duration_seconds is not None:
        note = (
            "Sample coverage is a rough check based on raw bytes saved versus session start/end time. "
            "It cannot prove frame continuity for legacy sessions without `ttl_frames.bin`."
        )
    else:
        note = (
            "Sample coverage is based on raw bytes saved only. Session timing was unavailable, "
            "so no coverage ratio was computed."
        )

    return SampleCoverageEstimate(
        total_samples=total_samples,
        saved_duration_seconds=saved_duration_seconds,
        estimated_frame_count_from_raw=estimated_frame_count_from_raw,
        trailing_partial_frame_bytes=trailing_partial_frame_bytes,
        expected_session_duration_seconds=expected_session_duration_seconds,
        expected_session_samples=expected_session_samples,
        session_sample_coverage_ratio=session_sample_coverage_ratio,
        note=note,
    )


def load_ttl_edges(
    ttl_raw_path: str | Path,
    ttl_meta: Dict[str, Any],
    progress: ProgressReporter = None,
) -> List[Dict[str, Any]]:
    raw_path = Path(ttl_raw_path)
    raw = raw_path.read_bytes()
    frames_path = raw_path.with_name("ttl_frames.bin")

    frame_size = int(ttl_meta["frame_size"])
    sample_rate_hz = int(ttl_meta["sampling_rate_hz"])
    t0_frame_id = int(ttl_meta.get("t0_frame_id", 0) or 0)
    t0_monotonic_ns = int(ttl_meta.get("t0_monotonic_ns", 0) or 0)
    channel_map = [int(v) for v in ttl_meta.get("channel_map", [1, 2, 3, 4])]

    edges: List[Dict[str, Any]] = []
    channels = min(4, len(channel_map))
    last_state = [0] * channels
    rise_at: Dict[int, int] = {}
    _emit_progress(progress, f"Loaded `{raw_path.name}` with {len(raw)} samples")

    if frames_path.exists():
        header, records = read_frame_index(frames_path)
        _emit_progress(progress, f"Using `{frames_path.name}` with {len(records)} indexed frame records")
        if header.frame_size != frame_size:
            raise ValueError(
                f"TTL frame index frame_size mismatch: meta={frame_size} index={header.frame_size}"
            )
        if header.sampling_rate_hz != sample_rate_hz:
            raise ValueError(
                f"TTL frame index sampling_rate_hz mismatch: meta={sample_rate_hz} index={header.sampling_rate_hz}"
            )
        report_every = max(1, len(records) // 20) if records else 1
        for record_idx, record in enumerate(records, start=1):
            offset = int(record.payload_offset_bytes)
            payload = raw[offset : offset + frame_size]
            if len(payload) != frame_size:
                raise ValueError(
                    f"TTL raw payload is truncated for frame_id={record.frame_id}: expected {frame_size} bytes, "
                    f"found {len(payload)}."
                )
            for edge in extract_edges_from_payload(
                payload=payload,
                sample_rate_hz=sample_rate_hz,
                frame_size=frame_size,
                frame_id_start=int(record.frame_id),
                channels=channels,
                last_state=last_state,
                rise_at=rise_at,
            ):
                edges.append(
                    _edge_entry(
                        edge=edge,
                        channel_map=channel_map,
                        t0_monotonic_ns=t0_monotonic_ns,
                        t0_frame_id=t0_frame_id,
                        frame_id=int(record.frame_id),
                        frame_size=frame_size,
                        sample_rate_hz=sample_rate_hz,
                    )
                )
            if len(records) and (record_idx == 1 or record_idx % report_every == 0 or record_idx == len(records)):
                _emit_progress(progress, f"Processed {record_idx}/{len(records)} indexed TTL frames")
    else:
        usable_bytes = (len(raw) // frame_size) * frame_size
        total_chunks = (usable_bytes // frame_size) if frame_size > 0 else 0
        report_every = max(1, total_chunks // 20) if total_chunks else 1
        _emit_progress(progress, f"No `ttl_frames.bin` present; scanning {total_chunks} contiguous raw frames")
        for chunk_index, offset in enumerate(range(0, usable_bytes, frame_size)):
            payload = raw[offset : offset + frame_size]
            frame_id = t0_frame_id + chunk_index
            for edge in extract_edges_from_payload(
                payload=payload,
                sample_rate_hz=sample_rate_hz,
                frame_size=frame_size,
                frame_id_start=frame_id,
                channels=channels,
                last_state=last_state,
                rise_at=rise_at,
            ):
                edges.append(
                    _edge_entry(
                        edge=edge,
                        channel_map=channel_map,
                        t0_monotonic_ns=t0_monotonic_ns,
                        t0_frame_id=t0_frame_id,
                        frame_id=frame_id,
                        frame_size=frame_size,
                        sample_rate_hz=sample_rate_hz,
                    )
                )
            if total_chunks and (chunk_index == 0 or (chunk_index + 1) % report_every == 0 or (chunk_index + 1) == total_chunks):
                _emit_progress(progress, f"Processed {chunk_index + 1}/{total_chunks} raw TTL frames")
    return edges


def _edge_entry(
    *,
    edge: TTLEdge,
    channel_map: List[int],
    t0_monotonic_ns: int,
    t0_frame_id: int,
    frame_id: int,
    frame_size: int,
    sample_rate_hz: int,
) -> Dict[str, Any]:
    monotonic_ns = reconstruct_timestamp_ns(
        t0_monotonic_ns=t0_monotonic_ns,
        t0_frame_id=t0_frame_id,
        frame_id=frame_id,
        sample_offset=edge.sample_index - frame_id * frame_size,
        frame_size=frame_size,
        sample_rate_hz=sample_rate_hz,
    )
    channel_name = f"ch{channel_map[edge.channel_index]}" if edge.channel_index < len(channel_map) else f"ch{edge.channel_index + 1}"
    return {
        "edge": edge,
        "monotonic_ns": monotonic_ns,
        "channel_name": channel_name,
        "pulse_width_ms": (edge.pulse_width_samples / float(sample_rate_hz)) * 1000.0,
    }


def _frame_gap_summary(records: List[TTLFrameIndexRecord]) -> tuple[List[str], int]:
    gap_ranges: List[str] = []
    missing_frame_count = 0
    prev_frame_id: Optional[int] = None
    for record in records:
        frame_id = int(record.frame_id)
        if prev_frame_id is not None and frame_id > prev_frame_id + 1:
            gap_start = prev_frame_id + 1
            gap_stop = frame_id - 1
            gap_ranges.append(
                f"{gap_start}" if gap_start == gap_stop else f"{gap_start}-{gap_stop}"
            )
            missing_frame_count += frame_id - prev_frame_id - 1
        prev_frame_id = frame_id
    return gap_ranges, missing_frame_count


def build_frequency_note(payload: Dict[str, Any]) -> Optional[str]:
    stimulus = payload.get("config", {}).get("stimulus", {})
    if not isinstance(stimulus, dict):
        return None
    train = stimulus.get("train", {})
    if not isinstance(train, dict):
        return None
    off_seconds = float(train.get("off_seconds", 0.0) or 0.0)
    if off_seconds <= 0:
        return None
    return (
        "This session uses train ON/OFF scheduling (`train.off_seconds > 0`). "
        "The reported `inferred_frequency_hz` is calculated across the full session/window, "
        "so OFF gaps lower the value relative to the within-burst pulse rate."
    )


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _emit_progress(progress: ProgressReporter, message: str) -> None:
    if progress is not None:
        progress(message)


def extract_command_windows(payload: Dict[str, Any]) -> List[CommandWindow]:
    grouped = payload.get("triggers_by_animal", {})
    if not isinstance(grouped, dict):
        return []

    open_loop_events = grouped.get("__open_loop__", [])
    if not isinstance(open_loop_events, list):
        return []

    active: Dict[str, Dict[str, Any]] = {}
    windows: List[CommandWindow] = []

    for event in open_loop_events:
        if not isinstance(event, dict):
            continue
        meta = event.get("meta", {})
        if not isinstance(meta, dict):
            continue
        event_channels = [str(ch).strip() for ch in meta.get("channels", []) if str(ch).strip()]
        monotonic_ns = meta.get("recorded_monotonic_ns")
        if monotonic_ns is None:
            continue
        try:
            recorded_monotonic_ns = int(monotonic_ns)
        except Exception:
            continue
        action = str(event.get("action", "")).lower()
        timestamp = str(event.get("timestamp", ""))
        reason = str(event.get("reason", ""))

        for channel_name in event_channels:
            if action == "start":
                active[channel_name] = {
                    "start_monotonic_ns": recorded_monotonic_ns,
                    "start_timestamp": timestamp,
                    "start_reason": reason,
                }
            elif action == "stop" and channel_name in active:
                start = active.pop(channel_name)
                windows.append(
                    CommandWindow(
                        channel_name=channel_name,
                        start_monotonic_ns=int(start["start_monotonic_ns"]),
                        stop_monotonic_ns=recorded_monotonic_ns,
                        start_timestamp=str(start["start_timestamp"]),
                        stop_timestamp=timestamp,
                        start_reason=str(start["start_reason"]),
                        stop_reason=reason,
                    )
                )
    return windows


def summarize_edges_by_channel(rising_edges: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_channel: Dict[str, List[Dict[str, Any]]] = {}
    for edge in rising_edges:
        by_channel.setdefault(edge["channel_name"], []).append(edge)

    summaries: Dict[str, Dict[str, Any]] = {}
    for channel_name, edges in sorted(by_channel.items()):
        monotonic_ns = [edge["monotonic_ns"] for edge in edges]
        pulse_width_ms = [float(edge["pulse_width_ms"]) for edge in edges if edge["pulse_width_ms"] > 0]
        inferred_frequency_hz = _frequency_from_monotonic_ns(monotonic_ns)
        summaries[channel_name] = {
            "rising_edges": len(edges),
            "inferred_frequency_hz": inferred_frequency_hz,
            "avg_pulse_width_ms": round(mean(pulse_width_ms), 3) if pulse_width_ms else None,
        }
    return summaries


def _verify_window(window: CommandWindow, window_rising: List[Dict[str, Any]]) -> WindowVerification:
    if not window_rising:
        return WindowVerification(
            channel_name=window.channel_name,
            start_monotonic_ns=window.start_monotonic_ns,
            stop_monotonic_ns=window.stop_monotonic_ns,
            pulse_count=0,
            first_pulse_latency_ms=None,
            last_pulse_before_stop_ms=None,
            inferred_frequency_hz=None,
            ok=False,
            note="No TTL rising edges detected in commanded window.",
        )

    monotonic_ns = [edge["monotonic_ns"] for edge in window_rising]
    first_latency_ms = (min(monotonic_ns) - window.start_monotonic_ns) / 1_000_000.0
    last_before_stop_ms = (window.stop_monotonic_ns - max(monotonic_ns)) / 1_000_000.0
    frequency_hz = _frequency_from_monotonic_ns(monotonic_ns)
    return WindowVerification(
        channel_name=window.channel_name,
        start_monotonic_ns=window.start_monotonic_ns,
        stop_monotonic_ns=window.stop_monotonic_ns,
        pulse_count=len(window_rising),
        first_pulse_latency_ms=round(first_latency_ms, 3),
        last_pulse_before_stop_ms=round(last_before_stop_ms, 3),
        inferred_frequency_hz=frequency_hz,
        ok=True,
        note="TTL activity detected inside commanded window.",
    )


def _frequency_from_monotonic_ns(monotonic_ns: List[int]) -> Optional[float]:
    if len(monotonic_ns) < 2:
        return None
    intervals_s = []
    for prev, curr in zip(monotonic_ns, monotonic_ns[1:]):
        delta_ns = curr - prev
        if delta_ns > 0:
            intervals_s.append(delta_ns / 1_000_000_000.0)
    if not intervals_s:
        return None
    avg_interval_s = mean(intervals_s)
    if avg_interval_s <= 0:
        return None
    return round(1.0 / avg_interval_s, 3)


def format_report(report: SessionVerificationReport) -> str:
    lines = [
        f"Session: {report.session_name}",
        f"Metadata: {report.metadata_path}",
        f"TTL capture available: {report.ttl_enabled}",
    ]
    if report.ttl_meta_path:
        lines.append(f"TTL meta: {report.ttl_meta_path}")
    if report.ttl_raw_path:
        lines.append(f"TTL raw: {report.ttl_raw_path}")
    lines.append(
        "Sample coverage: "
        f"total_samples={report.sample_coverage.total_samples} "
        f"saved_duration_s={_fmt_float(report.sample_coverage.saved_duration_seconds)} "
        f"estimated_frames_from_raw={report.sample_coverage.estimated_frame_count_from_raw} "
        f"trailing_partial_frame_bytes={report.sample_coverage.trailing_partial_frame_bytes}"
    )
    if report.sample_coverage.expected_session_duration_seconds is not None:
        lines.append(
            "Sample coverage vs session: "
            f"expected_duration_s={_fmt_float(report.sample_coverage.expected_session_duration_seconds)} "
            f"expected_samples={report.sample_coverage.expected_session_samples} "
            f"coverage_ratio={_fmt_float(report.sample_coverage.session_sample_coverage_ratio)}"
        )
    if report.continuity.frame_index_present:
        continuity_line = (
            "Frame continuity: "
            f"records={report.continuity.frame_records} "
            f"first_frame_id={report.continuity.first_frame_id} "
            f"last_frame_id={report.continuity.last_frame_id} "
            f"missing_frames={report.continuity.missing_frame_count}"
        )
        if report.continuity.gap_ranges:
            continuity_line += f" gap_ranges={','.join(report.continuity.gap_ranges)}"
        lines.append(continuity_line)
    else:
        lines.append("Frame continuity: `ttl_frames.bin` not present; continuity was not audited.")
    lines.append(
        "Summary: "
        f"windows_ok={report.windows_ok}/{report.windows_verified} "
        f"rising_edges={report.total_rising_edges} stray_rising_edges={report.stray_rising_edges}"
    )

    if report.channel_summaries:
        lines.append("Per-channel TTL summary:")
        for channel_name, summary in report.channel_summaries.items():
            lines.append(
                f"- {channel_name}: rising_edges={summary['rising_edges']} "
                f"inferred_frequency_hz={summary['inferred_frequency_hz']} "
                f"avg_pulse_width_ms={summary['avg_pulse_width_ms']}"
            )

    if report.window_results:
        lines.append("Command-window verification:")
        for result in report.window_results:
            lines.append(
                f"- {result.channel_name}: ok={result.ok} pulses={result.pulse_count} "
                f"first_latency_ms={result.first_pulse_latency_ms} "
                f"last_before_stop_ms={result.last_pulse_before_stop_ms} "
                f"inferred_frequency_hz={result.inferred_frequency_hz} note={result.note}"
            )

    if report.frequency_note:
        lines.append("Warnings:")
        lines.append(f"- {report.frequency_note}")
    lines.append("Estimator note:")
    lines.append(f"- {report.sample_coverage.note}")

    if report.issues:
        lines.append("Issues:")
        for issue in report.issues:
            lines.append(f"- {issue}")

    return "\n".join(lines)


def render_report(report: SessionVerificationReport) -> Group:
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold cyan", no_wrap=True)
    summary.add_column()
    summary.add_row("Session", str(report.session_name))
    summary.add_row("Metadata", _code(str(report.metadata_path)))
    summary.add_row("TTL Capture", "yes" if report.ttl_enabled else "no")
    if report.ttl_meta_path:
        summary.add_row("TTL Meta", _code(str(report.ttl_meta_path)))
    if report.ttl_raw_path:
        summary.add_row("TTL Raw", _code(str(report.ttl_raw_path)))

    coverage = Table(title="Sample Coverage", show_header=True, header_style="bold magenta")
    coverage.add_column("Metric")
    coverage.add_column("Value", overflow="fold")
    coverage.add_row("Total samples", str(report.sample_coverage.total_samples))
    coverage.add_row("Saved duration (s)", _fmt_float(report.sample_coverage.saved_duration_seconds))
    coverage.add_row("Estimated frames from raw", str(report.sample_coverage.estimated_frame_count_from_raw))
    coverage.add_row("Trailing partial bytes", str(report.sample_coverage.trailing_partial_frame_bytes))
    coverage.add_row(
        "Expected session duration (s)",
        _fmt_float(report.sample_coverage.expected_session_duration_seconds),
    )
    coverage.add_row("Expected session samples", str(report.sample_coverage.expected_session_samples))
    coverage.add_row("Coverage ratio", _fmt_float(report.sample_coverage.session_sample_coverage_ratio))

    continuity = Table(title="Frame Continuity", show_header=True, header_style="bold green")
    continuity.add_column("Metric")
    continuity.add_column("Value", overflow="fold")
    continuity.add_row("Frame index present", "yes" if report.continuity.frame_index_present else "no")
    continuity.add_row("Records", str(report.continuity.frame_records))
    continuity.add_row("First frame ID", str(report.continuity.first_frame_id))
    continuity.add_row("Last frame ID", str(report.continuity.last_frame_id))
    continuity.add_row("Missing frames", str(report.continuity.missing_frame_count))
    continuity.add_row(
        "Gap ranges",
        ",".join(report.continuity.gap_ranges) if report.continuity.gap_ranges else "-",
    )

    ttl_summary = Table(title="TTL Summary", show_header=True, header_style="bold blue")
    ttl_summary.add_column("Metric")
    ttl_summary.add_column("Value", overflow="fold")
    ttl_summary.add_row("Windows OK", f"{report.windows_ok}/{report.windows_verified}")
    ttl_summary.add_row("Rising edges", str(report.total_rising_edges))
    ttl_summary.add_row("Stray rising edges", str(report.stray_rising_edges))

    channel_table = Table(title="Per-Channel TTL", show_header=True, header_style="bold yellow")
    channel_table.add_column("Channel")
    channel_table.add_column("Rising Edges", justify="right")
    channel_table.add_column("Inferred Freq (Hz)", justify="right")
    channel_table.add_column("Avg Pulse Width (ms)", justify="right")
    if report.channel_summaries:
        for channel_name, summary_info in report.channel_summaries.items():
            channel_table.add_row(
                channel_name,
                str(summary_info["rising_edges"]),
                _fmt_float(summary_info["inferred_frequency_hz"]),
                _fmt_float(summary_info["avg_pulse_width_ms"]),
            )
    else:
        channel_table.add_row("-", "-", "-", "-")

    window_table = Table(title="Command Windows", show_header=True, header_style="bold white")
    window_table.add_column("Channel")
    window_table.add_column("OK")
    window_table.add_column("Pulses", justify="right")
    window_table.add_column("First Latency (ms)", justify="right")
    window_table.add_column("Last Before Stop (ms)", justify="right")
    window_table.add_column("Inferred Freq (Hz)", justify="right")
    window_table.add_column("Note", overflow="fold")
    if report.window_results:
        for result in report.window_results:
            window_table.add_row(
                result.channel_name,
                "yes" if result.ok else "no",
                str(result.pulse_count),
                _fmt_float(result.first_pulse_latency_ms),
                _fmt_float(result.last_pulse_before_stop_ms),
                _fmt_float(result.inferred_frequency_hz),
                result.note,
            )
    else:
        window_table.add_row("-", "-", "-", "-", "-", "-", "No command windows found.")

    notes = []
    if report.frequency_note:
        notes.append(report.frequency_note)
    notes.append(report.sample_coverage.note)
    notes_panel = Panel("\n".join(f"- {item}" for item in notes), title="Notes", border_style="cyan")

    if report.issues:
        issues_panel = Panel("\n".join(f"- {item}" for item in report.issues), title="Issues", border_style="red")
    else:
        issues_panel = Panel("No issues detected.", title="Issues", border_style="green")

    return Group(
        Panel(summary, title="TTL Session Verification", border_style="blue"),
        coverage,
        continuity,
        ttl_summary,
        channel_table,
        window_table,
        notes_panel,
        issues_panel,
    )


def _fmt_float(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}"


def _code(value: str) -> Text:
    return Text(value, style="bold cyan")

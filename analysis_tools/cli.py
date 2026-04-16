from __future__ import annotations

import argparse
from datetime import datetime

import polars as pl

from .session import load_analysis_session


def _print_progress(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def _print_window_summary(stimulation_windows: pl.DataFrame) -> None:
    if stimulation_windows.is_empty():
        print("No stimulation windows found.")
        return
    print("Windows:")
    rows = stimulation_windows.sort(["start_timestamp_utc", "channel_name"]).to_dicts()
    for idx, row in enumerate(rows, start=1):
        label = str(row.get("assignment_id") or row.get("channel_name") or f"window_{idx}")
        assigned = row.get("assigned_animal_ids") or []
        assigned_text = ", ".join(str(item) for item in assigned) if assigned else "-"
        print(
            f"  {idx}. {label} | {row.get('channel_name')} | "
            f"{row.get('start_timestamp_local')} -> {row.get('stop_timestamp_local')} | "
            f"RFIDs: {assigned_text}"
        )


def _print_ttl_qc_summary(ttl_qc: pl.DataFrame) -> None:
    if ttl_qc.is_empty():
        print("No TTL QC rows.")
        return
    print("TTL QC:")
    for row in ttl_qc.sort("channel_name").to_dicts():
        freq = row.get("observed_frequency_hz")
        width = row.get("observed_pulse_width_ms")
        freq_text = "-" if freq is None else f"{float(freq):.3f} Hz"
        width_text = "-" if width is None else f"{float(width):.3f} ms"
        print(
            f"  {row['channel_name']}: pulses={row['pulse_count']} "
            f"freq={freq_text} width={width_text} "
            f"expected_active={row['expected_active']} "
            f"startup_singleton={row['startup_singleton_artifact']} "
            f"note={row['note']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build analysis tables from a session folder.")
    parser.add_argument("session_dir", help="Path to a session directory containing session.yaml")
    parser.add_argument(
        "--local-timezone",
        help="IANA timezone for naive local timestamps, for example America/New_York",
    )
    parser.add_argument(
        "--output-dir",
        help="Optional output directory for parquet tables. Defaults to <session>/analysis/",
    )
    parser.add_argument(
        "--ttl-active-low",
        dest="ttl_active_low",
        action="store_true",
        default=True,
        help="Decode TTL so raw LOW means asserted/active. Default is enabled for current H11L1 hardware.",
    )
    parser.add_argument(
        "--ttl-active-high",
        dest="ttl_active_low",
        action="store_false",
        help="Decode TTL so raw HIGH means asserted/active.",
    )
    args = parser.parse_args()

    session = load_analysis_session(
        args.session_dir,
        local_timezone=args.local_timezone,
        ttl_active_low=args.ttl_active_low,
        progress=_print_progress,
    )
    out_dir = session.write_parquet(output_dir=args.output_dir, progress=_print_progress)
    _print_progress(f"Wrote parquet tables to {out_dir}")

    print(f"Session: {session.session_name}")
    print(f"Timezone: {session.local_timezone}")
    print(f"Temperature rows: {session.temperature.height}")
    print(f"Trigger events: {session.trigger_events.height}")
    print(f"Stim windows: {session.stimulation_windows.height}")
    print(f"TTL edges: {session.ttl_edges.height}")
    print(f"TTL pulses: {session.ttl_pulses.height}")
    _print_window_summary(session.stimulation_windows)
    _print_ttl_qc_summary(session.ttl_qc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

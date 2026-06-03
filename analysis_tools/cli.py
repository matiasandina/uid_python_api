from __future__ import annotations

import argparse
from datetime import datetime

import polars as pl
from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .session import load_analysis_session


CONSOLE = Console()


def _print_progress(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    CONSOLE.print(f"[dim][{stamp}][/dim] {message}")


def _fmt_code(value: object) -> str:
    return f"[bold cyan]`{value}`[/bold cyan]"


def _fmt_count(value: object) -> str:
    return _fmt_code(value)


def _fmt_float(value: object, suffix: str, decimals: int = 3) -> str:
    if value is None:
        return "[dim]-[/dim]"
    return _fmt_code(f"{float(value):.{decimals}f} {suffix}")


def _build_session_summary(
    *,
    session_name: str,
    local_timezone: str,
    temperature_rows: int,
    trigger_events: int,
    stimulation_windows: int,
    ttl_edges: int,
    ttl_pulses: int,
    out_dir: object,
) -> Panel:
    table = Table(show_header=False, expand=True, box=box.SIMPLE_HEAVY, padding=(0, 1))
    table.add_column("Field", style="bold green", no_wrap=True)
    table.add_column("Value")
    table.add_row("Session", _fmt_code(session_name))
    table.add_row("Timezone", _fmt_code(local_timezone))
    table.add_row("Output", _fmt_code(out_dir))
    table.add_row("Temperature rows", _fmt_count(temperature_rows))
    table.add_row("Trigger events", _fmt_count(trigger_events))
    table.add_row("Stim windows", _fmt_count(stimulation_windows))
    table.add_row("TTL edges", _fmt_count(ttl_edges))
    table.add_row("TTL pulses", _fmt_count(ttl_pulses))
    return Panel(table, title="[bold green]Analysis Summary[/bold green]", border_style="green", box=box.ROUNDED)


def _build_window_summary(stimulation_windows: pl.DataFrame) -> Panel:
    if stimulation_windows.is_empty():
        return Panel("No stimulation windows found.", title="[bold cyan]Stimulation Windows[/bold cyan]", border_style="cyan", box=box.ROUNDED)

    table = Table(title="Stimulation Windows", show_header=True, header_style="bold cyan", box=box.MINIMAL_DOUBLE_HEAD)
    table.add_column("#", justify="right", style="bold")
    table.add_column("Assignment", style="bold")
    table.add_column("Channel", style="magenta")
    table.add_column("Window", style="white")
    table.add_column("RFIDs", style="yellow")
    rows = stimulation_windows.sort(["start_timestamp_utc", "channel_name"]).to_dicts()
    for idx, row in enumerate(rows, start=1):
        label = str(row.get("assignment_id") or row.get("channel_name") or f"window_{idx}")
        assigned = row.get("assigned_animal_ids") or []
        assigned_text = ", ".join(str(item) for item in assigned) if assigned else "-"
        table.add_row(
            str(idx),
            _fmt_code(label),
            _fmt_code(row.get("channel_name")),
            Text(f"{row.get('start_timestamp_local')} -> {row.get('stop_timestamp_local')}"),
            _fmt_code(assigned_text),
        )
    return Panel(table, border_style="cyan", box=box.ROUNDED)


def _build_ttl_qc_summary(ttl_qc: pl.DataFrame) -> Panel:
    if ttl_qc.is_empty():
        return Panel("No TTL QC rows.", title="[bold magenta]TTL QC[/bold magenta]", border_style="magenta", box=box.ROUNDED)

    table = Table(title="TTL QC", show_header=True, header_style="bold magenta", box=box.MINIMAL_DOUBLE_HEAD)
    table.add_column("Channel", style="bold")
    table.add_column("Pulses", justify="right")
    table.add_column("Pulse Freq", justify="right")
    table.add_column("Effective Freq", justify="right")
    table.add_column("Width", justify="right")
    table.add_column("Expected", justify="center")
    table.add_column("Status", style="white")
    for row in ttl_qc.sort("channel_name").to_dicts():
        note = str(row.get("note") or "")
        status_style = "green"
        if "differs from pulse spec" in note or "unexpected pulses" in note:
            status_style = "yellow"
        if "no pulses found" in note:
            status_style = "red"
        expected_active = "yes" if row.get("expected_active") else "no"
        if row.get("startup_singleton_artifact"):
            expected_active = f"{expected_active} / startup"
        table.add_row(
            _fmt_code(row["channel_name"]),
            _fmt_count(row["pulse_count"]),
            _fmt_float(row.get("observed_frequency_hz"), "Hz"),
            _fmt_float(row.get("observed_effective_frequency_hz"), "Hz"),
            _fmt_float(row.get("observed_pulse_width_ms"), "ms"),
            _fmt_code(expected_active),
            f"[{status_style}]{note}[/{status_style}]",
        )
    return Panel(table, border_style="magenta", box=box.ROUNDED)


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
    CONSOLE.print(
        Group(
            _build_session_summary(
                session_name=session.session_name,
                local_timezone=session.local_timezone,
                temperature_rows=session.temperature.height,
                trigger_events=session.trigger_events.height,
                stimulation_windows=session.stimulation_windows.height,
                ttl_edges=session.ttl_edges.height,
                ttl_pulses=session.ttl_pulses.height,
                out_dir=out_dir,
            ),
            _build_window_summary(session.stimulation_windows),
            _build_ttl_qc_summary(session.ttl_qc),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

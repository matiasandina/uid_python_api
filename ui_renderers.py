from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping, Sequence

import yaml
from rich import box
from rich.columns import Columns
from rich.console import Group
from rich.layout import Layout
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text


@dataclass(frozen=True)
class MenuOption:
    label: str
    enabled: bool = True


@dataclass(frozen=True)
class StatusItem:
    label: str
    state: str
    detail: str


def _state_style(state: str) -> tuple[str, str]:
    state_upper = str(state).upper()
    if state_upper == "OK":
        return "bold green", "green"
    if state_upper in {"FAILED", "STOPPED", "FAILED CLOSED"}:
        return "bold red", "red"
    if state_upper in {"ACTIVE", "LASER ACTIVE"}:
        return "bold yellow", "yellow"
    if state_upper in {"COMPLETED", "READY"}:
        return "bold cyan", "cyan"
    if state_upper in {"DISABLED", "INACTIVE"}:
        return "bold grey50", "grey50"
    return "bold white", "white"


def _status_badge(state: str) -> Text:
    style, _ = _state_style(state)
    badge = Text(str(state).upper(), style=style)
    return badge


def _menu_panel(menu_options: Sequence[MenuOption], selected_index: int) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(width=4)
    table.add_column(ratio=1)
    table.add_row("", "[dim]Use Up/Down arrows and Enter. Or type number.[/dim]")
    table.add_row("", "")
    for index, option in enumerate(menu_options):
        prefix = ">" if index == selected_index else " "
        number_style = "bold white" if index == selected_index else "dim"
        label_style = "bold white" if index == selected_index else ("white" if option.enabled else "dim")
        table.add_row(
            Text(f"{prefix} {index + 1}.", style=number_style),
            Text(option.label, style=label_style),
        )
    return Panel(table, title="[bold cyan]Preflight Main Menu[/bold cyan]", border_style="cyan", box=box.ROUNDED)


def _status_panel(status_items: Sequence[StatusItem], title: str) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(ratio=2)
    table.add_column(width=14)
    table.add_column(ratio=3)
    for item in status_items:
        _, detail_color = _state_style(item.state)
        table.add_row(
            Text(item.label, style="bold grey70"),
            _status_badge(item.state),
            Text(item.detail, style=f"bold {detail_color}" if detail_color != "white" else "bold white"),
        )
    return Panel(table, title=title, border_style="green", box=box.ROUNDED)


def _summary_table(sections: Sequence[Mapping[str, object]]) -> Table:
    table = Table(
        show_header=False,
        expand=True,
        box=box.SIMPLE_HEAVY,
        padding=(0, 1),
    )
    table.add_column(style="bold grey70", width=24)
    table.add_column(style="bold white", ratio=1)
    for section in sections:
        title = str(section.get("title", "")).strip()
        rows = section.get("rows", [])
        if title:
            table.add_row(Text(title, style="bold cyan"), "")
        for row in rows if isinstance(rows, Iterable) else []:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            label = str(row[0])
            value = str(row[1])
            value_style = str(row[2]) if len(row) > 2 else "bold white"
            table.add_row(Text(label, style="bold grey70"), Text(value, style=value_style))
        table.add_row("", "")
    return table


def build_preflight_dashboard(
    menu_options: Sequence[MenuOption],
    selected_index: int,
    status_items: Sequence[StatusItem],
    summary_sections: Sequence[Mapping[str, object]],
) -> Group:
    menu_panel = _menu_panel(menu_options, selected_index)
    status_panel = _status_panel(status_items, "[bold green]Preflight Status[/bold green]")
    summary_panel = Panel(
        _summary_table(summary_sections),
        title="[bold magenta]Session Summary[/bold magenta]",
        border_style="magenta",
        box=box.ROUNDED,
    )
    right_column = Group(status_panel, summary_panel)
    return Group(Columns([menu_panel, right_column], equal=True, expand=True))


def build_launch_summary(
    readiness_items: Sequence[StatusItem],
    summary_sections: Sequence[Mapping[str, object]],
    warnings: Sequence[str] | None = None,
    note: str | None = None,
) -> Group:
    blocks = [
        _status_panel(readiness_items, "[bold cyan]Launch Readiness[/bold cyan]"),
        Panel(
            _summary_table(summary_sections),
            title="[bold magenta]Launch Summary[/bold magenta]",
            border_style="magenta",
            box=box.ROUNDED,
        ),
    ]
    if note:
        blocks.append(Panel(Text(note, style="bold yellow"), border_style="yellow", box=box.ROUNDED))
    if warnings:
        warning_table = Table.grid(expand=True)
        warning_table.add_column(ratio=1)
        for warning in warnings:
            warning_table.add_row(Text(f"- {warning}", style="bold yellow"))
        blocks.append(
            Panel(
                warning_table,
                title="[bold yellow]Warnings[/bold yellow]",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
    return Group(*blocks)


def build_config_view(
    config: Mapping[str, object],
    title: str = "Effective Config",
    note_lines: Sequence[str] | None = None,
) -> Panel | Group:
    dumped = yaml.safe_dump(dict(config), sort_keys=False, allow_unicode=False)
    syntax = Syntax(dumped, "yaml", line_numbers=True, word_wrap=False, theme="ansi_dark")
    config_panel = Panel(syntax, title=f"[bold cyan]{title}[/bold cyan]", border_style="cyan", box=box.ROUNDED)
    if not note_lines:
        return config_panel
    note_table = Table.grid(expand=True)
    note_table.add_column(ratio=1)
    for line in note_lines:
        note_table.add_row(Text.from_markup(str(line)))
    note_panel = Panel(
        note_table,
        title="[bold yellow]Channel Context[/bold yellow]",
        border_style="yellow",
        box=box.ROUNDED,
    )
    return Group(config_panel, note_panel)


def build_runtime_mock(
    *,
    state_label: str,
    laser_label: str,
    session_time: str,
    save_path: str,
    stim_rows: Sequence[tuple[str, str]],
    event_rows: Sequence[tuple[str, str]],
    started_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> Group:
    hero = Table.grid(expand=True)
    hero.add_column(ratio=1)
    hero.add_column(ratio=1)
    hero.add_row(
        Panel(Text(state_label, justify="center", style="bold white"), title="[bold cyan]Current State[/bold cyan]", border_style="cyan", box=box.ROUNDED),
        Panel(Text(laser_label, justify="center", style="bold white"), title="[bold yellow]Laser Output[/bold yellow]", border_style="yellow", box=box.ROUNDED),
    )

    details = Table(show_header=False, expand=True, box=box.SIMPLE_HEAVY, padding=(0, 1))
    details.add_column(style="bold grey70", width=20)
    details.add_column(style="bold white", ratio=1)
    details.add_row("Session Time", session_time)
    if started_at is not None:
        details.add_row("Started At", started_at.strftime("%Y-%m-%d %I:%M:%S%p").lower())
    if ends_at is not None:
        details.add_row("Ends At", ends_at.strftime("%Y-%m-%d %I:%M:%S%p").lower())
    details.add_row("Data Saved To", save_path)

    stim = Table(show_header=False, expand=True, box=box.SIMPLE_HEAVY, padding=(0, 1))
    stim.add_column(style="bold grey70", width=20)
    stim.add_column(style="bold white", ratio=1)
    for label, value in stim_rows:
        stim.add_row(label, value)

    events = Table(show_header=False, expand=True, box=box.SIMPLE_HEAVY, padding=(0, 1))
    events.add_column(style="bold grey70", width=20)
    events.add_column(style="bold white", ratio=1)
    for label, value in event_rows:
        events.add_row(label, value)

    return Group(
        hero,
        Panel(details, title="[bold green]Session Overview[/bold green]", border_style="green", box=box.ROUNDED),
        Columns(
            [
                Panel(stim, title="[bold magenta]Stimulation[/bold magenta]", border_style="magenta", box=box.ROUNDED),
                Panel(events, title="[bold cyan]Recent Events[/bold cyan]", border_style="cyan", box=box.ROUNDED),
            ],
            equal=True,
            expand=True,
        ),
    )


def build_runtime_dashboard(
    *,
    output_directory: str,
    device_summary: Sequence[tuple[str, str]],
    devices: Sequence[tuple[str, str, str, str, str]],
    animals: Sequence[tuple[str, str, str, str, str, str]],
    state_label: str,
    laser_label: str,
    session_time: str,
    save_path: str,
    stim_rows: Sequence[tuple[str, str]],
    event_rows: Sequence[tuple[str, str]],
) -> Layout:
    layout = Layout()
    layout.split(
        Layout(name="header", size=6),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="left", ratio=5),
        Layout(name="right", ratio=4, minimum_size=46),
    )
    layout["left"].split(
        Layout(name="devices", size=10),
        Layout(name="animals", ratio=1),
    )
    layout["right"].split(
        Layout(name="status", size=14),
        Layout(name="stim", size=10),
        Layout(name="events", ratio=1),
    )

    summary_table = Table(show_header=False, expand=True, box=box.SIMPLE_HEAVY, padding=(0, 1))
    summary_table.add_column(style="bold grey70", width=18)
    summary_table.add_column(style="bold white", ratio=1)
    for label, value in device_summary:
        summary_table.add_row(label, value)
    summary_table.add_row("Data Saved To", output_directory)

    devices_table = Table(expand=True, box=box.MINIMAL, header_style="bold magenta")
    devices_table.add_column("Device", style="bold")
    devices_table.add_column("Host:Port")
    devices_table.add_column("State", justify="center")
    devices_table.add_column("Scans", justify="right")
    devices_table.add_column("Last Scan")
    for row in devices:
        devices_table.add_row(*row)

    animals_table = Table(expand=True, box=box.MINIMAL, header_style="bold magenta")
    animals_table.add_column("RFID", style="bold")
    animals_table.add_column("Current Temp (C)", justify="right")
    animals_table.add_column("Last Scanned At")
    animals_table.add_column("Zone", justify="right")
    animals_table.add_column("Scans", justify="right")
    animals_table.add_column("Status")
    for row in animals:
        animals_table.add_row(*row)

    status = Table(show_header=False, expand=True, box=box.SIMPLE_HEAVY, padding=(0, 1))
    status.add_column(style="bold grey70", width=18)
    status.add_column(style="bold white", ratio=1)
    status.add_row("Current State", state_label)
    status.add_row("Laser Output", laser_label)
    status.add_row("Session Time", session_time)
    status.add_row("Data Saved To", save_path)

    stim = Table(show_header=False, expand=True, box=box.SIMPLE_HEAVY, padding=(0, 1))
    stim.add_column(style="bold grey70", width=18)
    stim.add_column(style="bold white", ratio=1)
    for label, value in stim_rows:
        stim.add_row(label, value)

    events = Table(show_header=False, expand=True, box=box.SIMPLE_HEAVY, padding=(0, 1))
    events.add_column(style="bold grey70", width=18)
    events.add_column(style="bold white", ratio=1)
    for label, value in event_rows:
        events.add_row(label, value)

    footer = Text("Last Updated  2026-04-03 04:00:00pm  |  Press Ctrl+C to stop", style="bold")

    layout["header"].update(Panel(summary_table, title="[bold green]Run Overview[/bold green]", border_style="green", box=box.ROUNDED))
    layout["left"]["devices"].update(Panel(devices_table, title="[bold magenta]Devices[/bold magenta]", border_style="magenta", box=box.ROUNDED))
    layout["left"]["animals"].update(Panel(animals_table, title="[bold magenta]Animals[/bold magenta]", border_style="magenta", box=box.ROUNDED))
    layout["right"]["status"].update(Panel(status, title="[bold green]Experiment Status[/bold green]", border_style="green", box=box.ROUNDED))
    layout["right"]["stim"].update(Panel(stim, title="[bold magenta]Stimulation[/bold magenta]", border_style="magenta", box=box.ROUNDED))
    layout["right"]["events"].update(Panel(events, title="[bold cyan]Recent Events[/bold cyan]", border_style="cyan", box=box.ROUNDED))
    layout["footer"].update(Panel(footer, border_style="cyan", box=box.ROUNDED))
    return layout

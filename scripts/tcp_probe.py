#!/usr/bin/env python3
from __future__ import annotations

import argparse
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class ProbeTarget:
    host: str
    port: int
    name: str

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"


def _load_yaml_mapping(path: Path, *, required: bool) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise FileNotFoundError(str(path))
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a top-level mapping: {path}")
    return data


def _targets_from_config(path: Path) -> list[ProbeTarget]:
    config = _load_yaml_mapping(path, required=True)
    devices = config.get("devices") or []
    if not isinstance(devices, list):
        raise ValueError("`devices` in config must be a list.")

    targets: list[ProbeTarget] = []
    for index, item in enumerate(devices, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"`devices[{index}]` must be a mapping.")
        host = str(item.get("host") or "").strip()
        port = item.get("port")
        name = str(item.get("name") or host or f"Reader-{index}").strip()
        if not host or port is None:
            continue
        targets.append(ProbeTarget(host=host, port=int(port), name=name))
    return targets


def _select_targets(args: argparse.Namespace) -> list[ProbeTarget]:
    if args.host or args.port:
        if not args.host or args.port is None:
            raise ValueError("Provide both positional `host` and `port`, or neither.")
        return [ProbeTarget(host=args.host, port=int(args.port), name=args.name or args.host)]

    targets = _targets_from_config(Path(args.config))
    if not targets:
        raise ValueError(f"No TCP reader endpoints found in `{args.config}`.")

    if args.all:
        return targets

    if args.device:
        matches = [
            target
            for target in targets
            if args.device in {target.name, target.host, target.endpoint}
        ]
        if not matches:
            available = ", ".join(target.name for target in targets)
            raise ValueError(f"No configured device matched `{args.device}`. Available: {available}")
        return matches

    if len(targets) == 1:
        return targets

    names = ", ".join(target.name for target in targets)
    raise ValueError(f"Multiple devices configured. Use `--all` or `--device`. Available: {names}")


def _summarize_chunks(chunks: Iterable[bytes], limit: int = 5) -> list[str]:
    lines: list[str] = []
    for chunk in list(chunks)[:limit]:
        text = chunk.decode("ascii", errors="replace").replace("\r", "\\r").replace("\n", "\\n")
        lines.append(text[:160])
    return lines


def _log_step(console: Console, args: argparse.Namespace, label: str, value: str, style: str = "cyan") -> None:
    if not args.verbose:
        return
    console.print(f"[bold]{escape(label)}[/bold] [{style}]`{escape(value)}`[/{style}]")


def _render_result(
    console: Console,
    target: ProbeTarget,
    *,
    connected: bool,
    command_sent: bool,
    bytes_received: int,
    chunks: list[bytes],
    elapsed: float,
    error: str | None,
    remote_closed: bool,
    success: bool,
) -> None:
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Device", f"[cyan]`{escape(target.name)}`[/cyan]")
    table.add_row("Endpoint", f"[cyan]`{escape(target.endpoint)}`[/cyan]")
    table.add_row("TCP connect", "[green]ok[/green]" if connected else "[red]failed[/red]")
    table.add_row("Command sent", "[green]yes[/green]" if command_sent else "[yellow]no[/yellow]")
    table.add_row("Bytes received", f"[cyan]`{bytes_received}`[/cyan]")
    table.add_row("Remote closed", "[yellow]yes[/yellow]" if remote_closed else "[cyan]`no`[/cyan]")
    table.add_row("Elapsed", f"[cyan]`{elapsed:.2f}s`[/cyan]")
    if error:
        table.add_row("Error", f"[red]`{escape(error)}`[/red]")

    if chunks:
        for index, line in enumerate(_summarize_chunks(chunks), start=1):
            table.add_row(f"Sample {index}", f"[cyan]`{escape(line)}`[/cyan]")

    status = "PASS" if success else "FAIL"
    style = "green" if connected else "red"
    if connected and not success:
        style = "yellow"
    console.print(Panel(table, title=f"[{style}]{status} {escape(target.name)}[/{style}]", border_style=style))


def probe_target(console: Console, target: ProbeTarget, args: argparse.Namespace) -> bool:
    connected = False
    command_sent = False
    chunks: list[bytes] = []
    bytes_received = 0
    error: str | None = None
    remote_closed = False
    started = time.monotonic()

    try:
        _log_step(console, args, "Target", f"{target.name} {target.endpoint}")
        try:
            address_infos = socket.getaddrinfo(target.host, target.port, type=socket.SOCK_STREAM)
            resolved = ", ".join(f"{info[4][0]}:{info[4][1]}" for info in address_infos[:3])
            _log_step(console, args, "Resolved address", resolved or "none")
        except OSError as exc:
            _log_step(console, args, "Address resolution failed", str(exc), "red")
            raise

        _log_step(console, args, "Opening TCP socket", f"timeout={args.timeout}s")
        with socket.create_connection((target.host, target.port), timeout=args.timeout) as sock:
            connected = True
            _log_step(console, args, "TCP connect", "ok", "green")
            sock.settimeout(args.timeout)

            if args.command:
                payload = args.command
                if args.crlf and not payload.endswith("\r\n"):
                    payload += "\r\n"
                printable_payload = payload.replace("\r", "\\r").replace("\n", "\\n")
                _log_step(console, args, "Sending command", printable_payload)
                sock.sendall(payload.encode("ascii"))
                command_sent = True
                _log_step(console, args, "Command send", f"{len(payload)} byte(s) sent", "green")
            else:
                _log_step(console, args, "Command send", "skipped (--no-command)", "yellow")

            deadline = time.monotonic() + args.listen_seconds
            _log_step(console, args, "Listening", f"{args.listen_seconds}s buffer={args.buffer_size}")
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(args.buffer_size)
                except socket.timeout:
                    _log_step(console, args, "Receive timeout", f"no bytes after {args.timeout}s")
                    continue
                if not chunk:
                    remote_closed = True
                    _log_step(console, args, "Receive", "remote closed connection", "yellow")
                    break
                chunks.append(chunk)
                bytes_received += len(chunk)
                preview = chunk.decode("ascii", errors="replace").replace("\r", "\\r").replace("\n", "\\n")
                _log_step(console, args, "Received chunk", f"{len(chunk)} byte(s): {preview[:120]}", "green")
                if args.once:
                    break
    except OSError as exc:
        error = str(exc)
        _log_step(console, args, "Socket error", error, "red")

    elapsed = time.monotonic() - started
    success = connected and (bytes_received > 0 if args.require_data else True)
    _render_result(
        console,
        target,
        connected=connected,
        command_sent=command_sent,
        bytes_received=bytes_received,
        chunks=chunks,
        elapsed=elapsed,
        error=error,
        remote_closed=remote_closed,
        success=success,
    )
    return success


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe UID reader TCP connectivity without starting the full runtime."
    )
    parser.add_argument("host", nargs="?", help="Reader IP/hostname. If omitted, load from config.")
    parser.add_argument("port", nargs="?", type=int, help="Reader TCP port. If omitted, load from config.")
    parser.add_argument("--name", help="Display name for a positional host/port target.")
    parser.add_argument("--config", default="config.local.yaml", help="Config file with devices[].")
    parser.add_argument("--device", help="Configured device name, host, or host:port to probe.")
    parser.add_argument("--all", action="store_true", help="Probe every configured device.")
    parser.add_argument("--timeout", type=float, default=5.0, help="Socket connect/read timeout in seconds.")
    parser.add_argument("--listen-seconds", type=float, default=5.0, help="How long to wait for reader data.")
    parser.add_argument("--buffer-size", type=int, default=1024, help="Socket receive buffer size.")
    parser.add_argument("--command", default="RRLOOP", help="ASCII command to send after connecting.")
    parser.add_argument("--no-command", dest="command", action="store_const", const=None, help="Only open TCP.")
    parser.add_argument("--no-crlf", dest="crlf", action="store_false", help="Do not append CRLF to command.")
    parser.add_argument("--once", action="store_true", help="Stop after the first received chunk.")
    parser.add_argument("--require-data", action="store_true", help="Exit nonzero unless bytes are received.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print each probe step as it happens.")
    parser.set_defaults(crlf=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    console = Console()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        targets = _select_targets(args)
    except Exception as exc:
        console.print(
            Panel(
                Text.from_markup(f"[bold red]Probe setup failed[/bold red]\n`{escape(str(exc))}`"),
                border_style="red",
            )
        )
        return 2

    if args.verbose:
        source = "positional host/port" if args.host else f"config {args.config}"
        console.print(f"[bold]Selected targets[/bold] [cyan]`{len(targets)}`[/cyan] from [cyan]`{escape(source)}`[/cyan]")
    results = [probe_target(console, target, args) for target in targets]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import copy
import io
import os
import re
import socket
import sys
import time
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import yaml

from data_parser import DataParser
from hardware_setup import (
    _build_channel_hw_map,
    connect_laser_for_run,
    list_serial_candidates,
    list_windows_usb_devices,
    probe_doric_ports,
    probe_teensy_handshake,
    resolve_active_stimulus_channels,
)
from live_state import LiveState, default_setup_state
from session_naming import build_session_folder_name


ValidatorFn = Callable[[Dict[str, Any]], Dict[str, Any]]


@dataclass(frozen=True)
class EditField:
    key: str
    path: Tuple[str, ...]
    kind: str
    label: str


EDITABLE_FIELDS: List[EditField] = [
    EditField("enabled", ("stimulus", "enabled"), "bool", "Stimulus enabled"),
    EditField("mode", ("stimulus", "mode"), "str", "Stimulus mode (monitor|laser)"),
    EditField("ttl_capture.enabled", ("ttl_capture", "enabled"), "bool", "TTL capture enabled"),
    EditField("dll_path", ("stimulus", "dll_path"), "str", "Stimulus DLL path"),
    EditField("uid", ("stimulus", "uid"), "str", "Stimulus UID"),
    EditField("port", ("stimulus", "port"), "int", "Stimulus port"),
    EditField("square.period_ms", ("stimulus", "square", "period_ms"), "float", "Square period (ms)"),
    EditField("square.time_on_ms", ("stimulus", "square", "time_on_ms"), "float", "Square time_on (ms)"),
    EditField("window_on_seconds", ("stimulus", "window_on_seconds"), "float", "Window on seconds"),
]


RUNTIME_ONLY_KEYS = {"_setup_state", "_preflight_driver", "laser_driver"}
MIN_UI_WIDTH = 132
MIN_UI_HEIGHT = 34


def _clone_without_runtime(obj: Any) -> Any:
    if isinstance(obj, dict):
        cloned: Dict[str, Any] = {}
        for key, value in obj.items():
            if str(key) in RUNTIME_ONLY_KEYS:
                continue
            cloned[key] = _clone_without_runtime(value)
        return cloned
    if isinstance(obj, list):
        return [_clone_without_runtime(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(_clone_without_runtime(item) for item in obj)
    return copy.deepcopy(obj)


def _copy_setup_state_without_runtime(config: Dict[str, Any]) -> Dict[str, Any]:
    setup_state = config.get("_setup_state", {})
    if not isinstance(setup_state, dict):
        return {}
    copied = _clone_without_runtime(setup_state)
    return copied if isinstance(copied, dict) else {}


def parse_bool_text(text: str) -> bool:
    value = text.strip().lower()
    truthy = {"1", "true", "yes", "y", "on"}
    falsy = {"0", "false", "no", "n", "off"}
    if value in truthy:
        return True
    if value in falsy:
        return False
    raise ValueError(f"Invalid boolean value: {text} (expected true/false, yes/no, 1/0)")


def parse_int_text(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Integer value cannot be empty")
    try:
        return int(stripped)
    except Exception as exc:
        raise ValueError(f"Invalid integer value: {text}") from exc


def parse_float_text(text: str) -> float:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Numeric value cannot be empty")
    try:
        return float(stripped)
    except Exception as exc:
        raise ValueError(f"Invalid numeric value: {text}") from exc


def compute_duty_cycle(square_cfg: Dict[str, Any]) -> Optional[float]:
    period_ms = float(square_cfg.get("period_ms", 0))
    time_on_ms = float(square_cfg.get("time_on_ms", 0))
    if period_ms <= 0:
        return None
    return time_on_ms / period_ms


def _get_stimulus_start_cfg(stim: Dict[str, Any]) -> Dict[str, Any]:
    start_cfg = stim.get("start")
    if isinstance(start_cfg, dict) and start_cfg:
        return dict(start_cfg)
    legacy_delay = stim.get("start_delay_seconds")
    if legacy_delay is not None:
        return {
            "mode": "delay",
            "timezone": None,
            "at_hhmm": None,
            "rollback_next_day": False,
            "delay_seconds": legacy_delay,
        }
    return {
        "mode": "immediate",
        "timezone": None,
        "at_hhmm": None,
        "rollback_next_day": False,
        "delay_seconds": None,
    }


def _resolve_stimulus_start(launch_time: datetime, stim: Dict[str, Any]) -> Tuple[str, float, datetime, str]:
    start_cfg = _get_stimulus_start_cfg(stim)
    mode = str(start_cfg.get("mode", "immediate")).strip().lower()
    if mode == "delay":
        delay_seconds = float(start_cfg.get("delay_seconds", 0.0) or 0.0)
        scheduled_at = launch_time + timedelta(seconds=delay_seconds)
        return mode, delay_seconds, scheduled_at, f"delay {delay_seconds:g} s"
    if mode == "clock":
        timezone_name = str(start_cfg.get("timezone") or "").strip()
        at_hhmm = str(start_cfg.get("at_hhmm") or "").strip()
        rollback_next_day = bool(start_cfg.get("rollback_next_day", False))
        zone = ZoneInfo(timezone_name)
        hour_text, minute_text = at_hhmm.split(":", 1)
        if launch_time.tzinfo is None:
            launch_local = launch_time.replace(tzinfo=zone)
        else:
            launch_local = launch_time.astimezone(zone)
        scheduled_local = launch_local.replace(
            hour=int(hour_text),
            minute=int(minute_text),
            second=0,
            microsecond=0,
        )
        if scheduled_local < launch_local:
            if not rollback_next_day:
                raise ValueError("clock start is earlier than the current launch time and rollback_next_day is false.")
            scheduled_local = scheduled_local + timedelta(days=1)
        scheduled_at = scheduled_local
        delay_seconds = max(0.0, (scheduled_at - launch_local).total_seconds())
        detail = f"clock {at_hhmm} {timezone_name} rollback_next_day={str(rollback_next_day).lower()}"
        return mode, delay_seconds, scheduled_at, detail
    return "immediate", 0.0, launch_time, "immediate"


def _format_stimulus_start(launch_time: datetime, stim: Dict[str, Any]) -> Tuple[str, str, str]:
    try:
        mode, delay_seconds, scheduled_at, detail = _resolve_stimulus_start(launch_time, stim)
    except Exception as exc:
        return "invalid", "invalid", str(exc)
    return (
        mode,
        scheduled_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
        detail if mode != "delay" else f"{detail} -> {scheduled_at.strftime('%Y-%m-%d %H:%M:%S %Z')}",
    )


def build_square_wave_preview(period_ms: Any, time_on_ms: Any, width: int = 40) -> str:
    """Build an ASCII one-period square waveform preview."""
    try:
        period = float(period_ms)
        time_on = float(time_on_ms)
    except Exception:
        return "[invalid waveform values]"

    if period <= 0 or time_on < 0:
        return "[invalid waveform values]"

    duty = min(max(time_on / period, 0.0), 1.0)
    on_chars = int(round(duty * width))
    off_chars = max(0, width - on_chars)
    bar = "#" * on_chars + "." * off_chars
    off_ms = max(0.0, period - time_on)
    freq_hz = 1000.0 / period
    return f"|{bar}| on={time_on:.1f}ms off={off_ms:.1f}ms period={period:.1f}ms freq={freq_hz:.2f}Hz"


def build_square_wave_plot(period_ms: Any, time_on_ms: Any, cycles: int = 4, cols_per_cycle: int = 12) -> List[str]:
    """Render a multi-cycle ASCII square-wave step plot with voltage and time axes."""
    try:
        period = float(period_ms)
        time_on = float(time_on_ms)
    except Exception:
        return ["[invalid waveform values]"]

    if period <= 0 or time_on < 0:
        return ["[invalid waveform values]"]

    duty = min(max(time_on / period, 0.0), 1.0)
    high_cols = max(0, min(cols_per_cycle, int(round(cols_per_cycle * duty))))
    low_cols = max(0, cols_per_cycle - high_cols)

    wave = []
    for _ in range(cycles):
        wave.extend([1] * high_cols)
        wave.extend([0] * low_cols)

    width = len(wave)
    top = ["_" if level == 1 else " " for level in wave]
    bottom = ["_" if level == 0 else " " for level in wave]
    mid = ["|" if i > 0 and wave[i] != wave[i - 1] else " " for i in range(width)]

    total_ms = period * cycles
    axis = "-" * width
    labels = ("0ms").ljust(max(4, width - 6)) + f"{total_ms:.0f}ms"

    return [
        f"5V |{''.join(top)}",
        f"   |{''.join(mid)}",
        f"0V |{''.join(bottom)}",
        f"   +{axis}> time",
        f"   {labels}",
    ]



def build_train_envelope_plot(on_seconds: Any, off_seconds: Any, cycles: int = 2, cols_per_cycle: int = 30) -> List[str]:
    """Render ON/OFF train envelope across multiple cycles."""
    try:
        on_s = float(on_seconds)
        off_s = float(off_seconds)
    except Exception:
        return ["[invalid train values]"]

    if on_s <= 0 or off_s < 0:
        return ["[invalid train values]"]

    cycle_s = on_s + off_s
    on_ratio = on_s / cycle_s if cycle_s > 0 else 1.0
    on_cols = max(1, min(cols_per_cycle, int(round(cols_per_cycle * on_ratio))))
    off_cols = max(0, cols_per_cycle - on_cols)

    wave = []
    for _ in range(cycles):
        wave.extend([1] * on_cols)
        wave.extend([0] * off_cols)

    width = len(wave)
    top = ["_" if level == 1 else " " for level in wave]
    bottom = ["_" if level == 0 else " " for level in wave]
    mid = ["|" if i > 0 and wave[i] != wave[i - 1] else " " for i in range(width)]

    total_s = cycle_s * cycles
    axis = "-" * width
    labels = ("0.0s").ljust(max(6, width - 8)) + f"{total_s:.1f}s"

    return [
        f"ON  |{''.join(top)}",
        f"    |{''.join(mid)}",
        f"OFF |{''.join(bottom)}",
        f"    +{axis}> time",
        f"    {labels}",
    ]


def summarize_channels(channels: Dict[str, Any]) -> str:
    if not channels:
        return "(none)"

    return ", ".join(str(name) for name in channels.keys())


def summarize_channel_currents(channels: Dict[str, Any]) -> str:
    if not channels:
        return "(none)"
    parts = []
    for name, ch in channels.items():
        if isinstance(ch, dict):
            parts.append(f"{name}: {ch.get('current_ma', '?')} mA")
        else:
            parts.append(str(name))
    return ", ".join(parts) if parts else "(none)"


def summarize_channel_list(channels: Any) -> str:
    if not isinstance(channels, list):
        return "(none)"
    names = [str(name).strip() for name in channels if str(name).strip()]
    return ", ".join(names) if names else "(none)"


def get_closed_loop_rules(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    rules = config.get("closed_loop", {}).get("rules", [])
    return list(rules) if isinstance(rules, list) else []


def get_open_loop_assignments(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    assignments = config.get("stimulus", {}).get("open_loop_assignments", [])
    return list(assignments) if isinstance(assignments, list) else []


def summarize_closed_loop_rule_outputs(config: Dict[str, Any]) -> str:
    channels: List[str] = []
    for rule in get_closed_loop_rules(config):
        outputs = rule.get("outputs", {})
        if isinstance(outputs, dict):
            channels.extend(outputs.get("laser_channels", []))
    deduped: List[str] = []
    seen: set[str] = set()
    for name in channels:
        text = str(name).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return ", ".join(deduped) if deduped else "(none)"


def get_closed_loop_output_channels(config: Dict[str, Any]) -> List[str]:
    text = summarize_closed_loop_rule_outputs(config)
    if text == "(none)":
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def summarize_closed_loop_rules(config: Dict[str, Any]) -> str:
    parts: List[str] = []
    for rule in get_closed_loop_rules(config):
        classifier = rule.get("classifier", {})
        outputs = rule.get("outputs", {})
        devices = summarize_channel_list(rule.get("devices"))
        channels = summarize_channel_list(outputs.get("laser_channels"))
        plugin = str(classifier.get("plugin") or "").strip() or "[not configured]"
        parts.append(f"{rule.get('id', '?')} [{devices}] -> {channels} via {plugin}")
    return " | ".join(parts) if parts else "(none)"


def _channel_context(config: Dict[str, Any]) -> Dict[str, Any]:
    stim = config.get("stimulus", {})
    control_mode = str(stim.get("control_mode", "closed_loop")).lower()
    closed_loop_rules = get_closed_loop_rules(config)
    trigger_enabled = bool(closed_loop_rules)
    open_loop_channels = [str(ch).strip() for ch in stim.get("target_channels", []) if str(ch).strip()]
    trigger_channels = get_closed_loop_output_channels(config)
    if control_mode == "open_loop":
        active_label = "Active Stimulation Channels"
        active_channels = open_loop_channels
        other_label = "Closed-Loop Output Channels"
        other_channels = trigger_channels
    else:
        active_label = "Closed-Loop Output Channels"
        active_channels = trigger_channels
        other_label = "Configured Open-Loop Channels"
        other_channels = open_loop_channels
    return {
        "control_mode": control_mode,
        "trigger_enabled": trigger_enabled,
        "active_label": active_label,
        "active_channels": active_channels,
        "other_label": other_label,
        "other_channels": other_channels,
        "show_note": bool(other_channels and active_channels != other_channels),
    }


def _channel_context_note_lines(config: Dict[str, Any]) -> List[str]:
    context = _channel_context(config)
    lines = [
        f"Run Mode: [bold cyan]{_format_run_mode_label(context['control_mode'])}[/bold cyan]",
        (
            "Temperature-Dependent Triggering: "
            + ("[bold green]On[/bold green]" if context["trigger_enabled"] else "[bold yellow]Off[/bold yellow]")
        ),
        f"{context['active_label']}: [bold white]{summarize_channel_list(context['active_channels'])}[/bold white]",
    ]
    if context["other_channels"]:
        lines.append(
            f"{context['other_label']}: [bold white]{summarize_channel_list(context['other_channels'])}[/bold white]"
        )
    if context["show_note"]:
        lines.extend(
            [
                "[yellow]Sanity note:[/yellow]",
                (
                    f"This run will stimulate [bold white]{summarize_channel_list(context['active_channels'])}[/bold white]."
                ),
                (
                    f"The config also contains {context['other_label'].lower()} "
                    f"[bold white]{summarize_channel_list(context['other_channels'])}[/bold white]."
                ),
                "Continue only if that separation is intentional.",
            ]
        )
    return lines


def build_doric_probe_candidates(stimulus_cfg: Dict[str, Any]) -> List[int]:
    discovery_cfg = stimulus_cfg.get("discovery", {})
    if not isinstance(discovery_cfg, dict):
        discovery_cfg = {}

    candidates: List[int] = []
    configured_port = int(stimulus_cfg.get("port", 0) or 0)
    if configured_port > 0:
        candidates.append(configured_port)

    explicit_candidates = discovery_cfg.get("candidate_ports", [])
    if isinstance(explicit_candidates, list):
        for value in explicit_candidates:
            try:
                port = int(value)
            except Exception:
                continue
            if port > 0:
                candidates.append(port)

    probe_min = int(discovery_cfg.get("probe_min_port", 1) or 1)
    probe_max = int(discovery_cfg.get("probe_max_port", 8) or 8)
    if probe_min > 0 and probe_max >= probe_min:
        candidates.extend(range(probe_min, probe_max + 1))

    deduped: List[int] = []
    seen = set()
    for port in candidates:
        if port in seen:
            continue
        seen.add(port)
        deduped.append(port)
    return deduped


def split_doric_probe_candidates(stimulus_cfg: Dict[str, Any]) -> Tuple[List[int], List[int]]:
    discovery_cfg = stimulus_cfg.get("discovery", {})
    if not isinstance(discovery_cfg, dict):
        discovery_cfg = {}

    preferred: List[int] = []
    configured_port = int(stimulus_cfg.get("port", 0) or 0)
    if configured_port > 0:
        preferred.append(configured_port)

    explicit_candidates = discovery_cfg.get("candidate_ports", [])
    if isinstance(explicit_candidates, list):
        for value in explicit_candidates:
            try:
                port = int(value)
            except Exception:
                continue
            if port > 0:
                preferred.append(port)

    probe_min = int(discovery_cfg.get("probe_min_port", 1) or 1)
    probe_max = int(discovery_cfg.get("probe_max_port", 8) or 8)
    fallback = list(range(probe_min, probe_max + 1)) if probe_min > 0 and probe_max >= probe_min else []

    preferred_deduped: List[int] = []
    seen = set()
    for port in preferred:
        if port in seen:
            continue
        seen.add(port)
        preferred_deduped.append(port)

    fallback_deduped: List[int] = []
    for port in fallback:
        if port in seen:
            continue
        seen.add(port)
        fallback_deduped.append(port)

    return preferred_deduped, fallback_deduped


def summarize_windows_usb_matches(matches: List[Any]) -> List[str]:
    if not matches:
        return ["No matching Windows USB devices found for the configured filter."]
    return [match.label() for match in matches]


def _build_doric_failure_lines(
    candidate_ports: List[int],
    invalid_results: List[Any],
    usb_matches: List[Any],
    discovery_cfg: Dict[str, Any],
    *,
    title: str,
) -> List[str]:
    lines = [title]
    lines.append(f"Probe candidates: {', '.join(str(port) for port in candidate_ports) or '[none]'}")
    lines.append(
        "Recovery: set stimulus.discovery.candidate_ports to a known-good port, "
        "or widen stimulus.discovery.probe_max_port and rerun Find Doric Device."
    )
    if discovery_cfg.get("usb_vid") or discovery_cfg.get("usb_pid"):
        lines.append("Matching Windows USB devices:")
        lines.extend(summarize_windows_usb_matches(usb_matches))
    lines.extend(
        [
            (
                f"Port {result.port}: open={result.open_result} "
                f"close={result.close_result} error={result.error or '-'}"
            )
            for result in invalid_results
        ]
    )
    return lines


def get_stim_warnings(stim_cfg: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    enabled = bool(stim_cfg.get("enabled", False))
    mode = str(stim_cfg.get("mode", "monitor")).strip().lower()
    control_mode = str(stim_cfg.get("control_mode", "closed_loop")).strip().lower()

    if enabled and mode == "laser":
        warnings.append("LASER RUN: real hardware setup is required before launch")
        if control_mode == "open_loop":
            warnings.append("OPEN LOOP LASER: output can begin on schedule after setup completes")
        else:
            warnings.append("CLOSED LOOP LASER: trigger events can issue outputs after setup completes")
    return warnings


def _laser_setup_required(config: Dict[str, Any]) -> bool:
    stim = config.get("stimulus", {})
    return bool(stim.get("enabled", False)) and str(stim.get("mode", "monitor")).strip().lower() == "laser"


def _teensy_setup_required(config: Dict[str, Any]) -> bool:
    return _laser_setup_required(config) and bool(config.get("ttl_capture", {}).get("enabled", False))



def _get_path(config: Dict[str, Any], path: Tuple[str, ...]) -> Any:
    node: Any = config
    for part in path:
        node = node[part]
    return node


def _set_path(config: Dict[str, Any], path: Tuple[str, ...], value: Any) -> None:
    node: Any = config
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value


def _parse_field_value(field: EditField, raw_value: str) -> Any:
    if field.kind == "bool":
        return parse_bool_text(raw_value)
    if field.kind == "int":
        return parse_int_text(raw_value)
    if field.kind == "float":
        return parse_float_text(raw_value)
    if field.kind == "str":
        value = raw_value.strip()
        if not value:
            raise ValueError("Value cannot be empty")
        return value
    raise ValueError(f"Unsupported field type: {field.kind}")


def _prepare_for_validate(config: Dict[str, Any]) -> Dict[str, Any]:
    prepared = _clone_without_runtime(config)
    channels = prepared.get("stimulus", {}).get("channels")
    if isinstance(channels, dict):
        denorm: Dict[str, Any] = {}
        for name, entry in channels.items():
            if isinstance(entry, dict):
                d = dict(entry)
                idx = int(d.get("index", 0))
                if 0 <= idx <= 7:
                    d["index"] = idx + 1
                denorm[name] = d
            else:
                try:
                    numeric = int(entry)
                except Exception:
                    denorm[name] = entry
                    continue
                if 0 <= numeric <= 7:
                    denorm[name] = numeric + 1
                else:
                    denorm[name] = numeric
        prepared["stimulus"]["channels"] = denorm
    return prepared


def _apply_validation(candidate: Dict[str, Any], validator: Optional[ValidatorFn]) -> Dict[str, Any]:
    if validator is None:
        return candidate
    prepared = _prepare_for_validate(candidate)
    return validator(prepared)


def _try_load_rich() -> Any:
    try:
        from rich.console import Console
    except ImportError:
        return None
    return Console()


def _terminal_size_ok(console: Any, *, min_width: int = MIN_UI_WIDTH, min_height: int = MIN_UI_HEIGHT) -> bool:
    if console is None:
        return True
    try:
        size = console.size
    except Exception:
        return True
    return size.width >= min_width and size.height >= min_height


def _wait_for_terminal_size(console: Any, *, min_width: int = MIN_UI_WIDTH, min_height: int = MIN_UI_HEIGHT) -> None:
    if console is None:
        return
    from rich.panel import Panel
    from rich.text import Text

    while not _terminal_size_ok(console, min_width=min_width, min_height=min_height):
        size = console.size
        message = Text()
        message.append("Terminal too small for the full monitoring UI.\n\n", style="bold yellow")
        message.append("Please enlarge the terminal window or reduce terminal font size until the UI fits.\n\n", style="white")
        message.append("Recommended minimum size:\n", style="bold white")
        message.append(f"- Width: {min_width} columns\n", style="bold cyan")
        message.append(f"- Height: {min_height} rows\n\n", style="bold cyan")
        message.append("Current size:\n", style="bold white")
        message.append(f"- Width: {size.width} columns\n", style="white")
        message.append(f"- Height: {size.height} rows\n\n", style="white")
        message.append("Windows tip:\n", style="bold white")
        message.append("- In native cmd.exe, Ctrl+- reduces zoom and Ctrl++ increases zoom.\n", style="white")
        message.append("- If the UI looks cramped, reduce zoom or maximize the window.\n\n", style="white")
        message.append("The UI will continue automatically once the terminal is large enough.", style="bold green")
        console.clear()
        console.print(
            Panel.fit(
                message,
                title="[bold yellow]Resize Terminal[/bold yellow]",
                border_style="yellow",
            )
        )
        time.sleep(0.25)


def _build_launch_contract_lines(config: Dict[str, Any]) -> List[str]:
    stim = config["stimulus"]
    ttl_cfg = config.get("ttl_capture", {})
    control_mode = str(stim.get("control_mode", "closed_loop")).lower()
    square = stim.get("square", {})
    pulse = stim.get("pulse", {})
    train = stim.get("train", {})
    derived = stim.get("derived", {})
    mode = str(stim.get("mode", "monitor")).lower()
    active_channels = resolve_active_stimulus_channels(config)
    output_behavior = "ACTIVE OUTPUTS POSSIBLE" if (bool(stim.get("enabled")) and mode == "laser") else "MONITOR ONLY"

    period_ms = pulse.get("period_ms", square.get("period_ms"))
    time_on_ms = pulse.get("time_on_ms", square.get("time_on_ms"))
    pulse_hz = derived.get("pulse_frequency_hz")
    train_on = train.get("on_seconds", stim.get("window_on_seconds"))
    train_off = train.get("off_seconds", 0.0)
    start_mode, start_at_text, start_detail = _format_stimulus_start(datetime.now(), stim)

    lines = [
        "[bold cyan]Launch Contract[/bold cyan]",
        f"run_type={control_mode}  stim_mode={mode}  output_behavior={output_behavior}",
        f"resolved_channels={summarize_channels(active_channels)}",
        f"pulse_hz={pulse_hz}  pulse_period_ms={period_ms}  pulse_time_on_ms={time_on_ms}",
        f"train_on_seconds={train_on}  train_off_seconds={train_off}",
        (
            "ttl_capture="
            f"enabled={ttl_cfg.get('enabled')} "
            f"port={ttl_cfg.get('port') or '[select in preflight]'}"
        ),
    ]
    if control_mode == "open_loop":
        lines.append(
            "run_window="
            f"minutes={stim.get('run_for_minutes')} "
            f"start_mode={start_mode} "
            f"start_at={start_at_text} "
            f"start_detail={start_detail}"
        )
    else:
        lines.append(
            "closed_loop_rules="
            f"{summarize_closed_loop_rules(config)}"
        )
    return lines


def _format_run_mode_label(control_mode: str) -> str:
    return "Open Loop" if control_mode == "open_loop" else "Closed Loop"


def _format_output_behavior(stim: Dict[str, Any]) -> str:
    if bool(stim.get("enabled")) and str(stim.get("mode", "monitor")).lower() == "laser":
        return "Real laser output possible"
    return "Monitor only"


def _format_classifier_label(classifier_spec: str) -> str:
    spec = classifier_spec.strip()
    if not spec:
        return "not configured"
    if ":" in spec:
        module_name, func_name = spec.split(":", 1)
        short_module = module_name.split(".")[-1]
        return f"{short_module}:{func_name}"
    return spec.split(".")[-1]


def _build_closed_loop_summary_rows(config: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    rows: List[Tuple[str, str, str]] = []
    for rule in get_closed_loop_rules(config):
        classifier_cfg = rule.get("classifier", {})
        outputs = rule.get("outputs", {})
        rows.append((f"Rule {rule.get('id', '?')}", summarize_channel_list(outputs.get("laser_channels")), "bold white"))
        rows.append((f"{rule.get('id', '?')} Devices", summarize_channel_list(rule.get("devices")), "bold white"))
        rows.append((f"{rule.get('id', '?')} Classifier", _format_classifier_label(str(classifier_cfg.get("plugin") or "")), "bold white"))
        rows.append(
            (
                f"{rule.get('id', '?')} Eval",
                f"every {classifier_cfg.get('evaluate_interval_seconds')} s / window {classifier_cfg.get('clf_data_input_window_seconds')} s",
                "bold white",
            )
        )
        threshold = classifier_cfg.get("config", {}).get("threshold_c")
        if threshold is not None:
            rows.append((f"{rule.get('id', '?')} Threshold", f"{threshold} C", "bold white"))
    return rows


def _build_session_summary_sections(config: Dict[str, Any], live_state: LiveState) -> List[Dict[str, Any]]:
    stim = config.get("stimulus", {})
    ttl_cfg = config.get("ttl_capture", {})
    setup = _ensure_setup_state(live_state)
    control_mode = str(stim.get("control_mode", "closed_loop")).lower()
    square = stim.get("square", {})
    pulse = stim.get("pulse", {})
    train = stim.get("train", {})
    derived = stim.get("derived", {})
    active_channels = resolve_active_stimulus_channels(config)
    channel_context = _channel_context(config)

    period_ms = pulse.get("period_ms", square.get("period_ms"))
    time_on_ms = pulse.get("time_on_ms", square.get("time_on_ms"))
    pulse_off_ms = None
    try:
        pulse_off_ms = float(period_ms) - float(time_on_ms)
    except Exception:
        pulse_off_ms = None
    train_on = train.get("on_seconds", stim.get("window_on_seconds"))
    train_off = train.get("off_seconds", 0.0)
    pulse_hz = derived.get("pulse_frequency_hz")
    start_mode, start_at_text, start_detail = _format_stimulus_start(datetime.now(), stim)

    sections: List[Dict[str, Any]] = [
        {
            "title": "Experiment",
            "rows": [
                ("Run Mode", _format_run_mode_label(control_mode), "bold cyan"),
                ("Session Label", live_state.session_label or "[blank -> timestamp only]", "bold white"),
                ("Output", _format_output_behavior(stim), "bold yellow" if "possible" in _format_output_behavior(stim) else "bold white"),
                (channel_context["active_label"], summarize_channels(active_channels), "bold white"),
                ("Temperature Triggering", "On" if channel_context["trigger_enabled"] else "Off", "bold green" if channel_context["trigger_enabled"] else "bold yellow"),
                (
                    channel_context["other_label"],
                    summarize_channel_list(channel_context["other_channels"]),
                    "bold white",
                ),
                ("Data Saved To", str(config.get("output_directory", "")), "bold green"),
            ],
        },
        {
            "title": "Stimulation",
            "rows": [
                ("Pulse Frequency", f"{pulse_hz} Hz" if pulse_hz is not None else "not resolved", "bold white"),
                (
                    "Pulse Timing",
                    f"{time_on_ms} ms ON / {pulse_off_ms:g} ms OFF" if pulse_off_ms is not None else "not resolved",
                    "bold white",
                ),
                (
                    "Train Timing",
                    f"{train_on} s ON / {train_off} s OFF"
                    + ("  !! 100% duty cycle" if float(train_off or 0) == 0 else ""),
                    "bold red" if float(train_off or 0) == 0 else "bold white",
                ),
                ("Laser Current", summarize_channel_currents(stim.get("channels", {})), "bold white"),
            ],
        },
    ]

    control_rows: List[Tuple[str, str, str]] = [
        ("TTL Capture", f"{'Enabled' if ttl_cfg.get('enabled') else 'Disabled'} on {ttl_cfg.get('port') or '[select in preflight]'}", "bold white"),
        ("Laser Setup", str(setup.get("laser_state", "not_started")).replace("_", " ").title(), "bold white"),
        ("Teensy Setup", str(setup.get("teensy_state", "not_started")).replace("_", " ").title(), "bold white"),
    ]
    if control_mode == "open_loop":
        control_rows.insert(0, ("Total Session Time", "Manual exit after scheduled start + planned stimulation", "bold white"))
        control_rows.insert(1, ("Planned Stim Duration", f"{stim.get('run_for_minutes')} min", "bold white"))
        control_rows.insert(2, ("Start Mode", start_mode, "bold yellow"))
        control_rows.insert(3, ("Scheduled Start", start_at_text, "bold yellow"))
        control_rows.insert(4, ("Start Detail", start_detail, "bold white"))
    else:
        control_rows.insert(0, ("Total Session Time", "Manual exit / trigger-driven", "bold white"))
        control_rows.extend(_build_closed_loop_summary_rows(config))
    sections.append({"title": "Control", "rows": control_rows})
    if channel_context["show_note"]:
        sections.append(
            {
                "title": "Sanity Note",
                "rows": [
                    (
                        "This Run",
                        f"Will stimulate {summarize_channel_list(channel_context['active_channels'])}",
                        "bold white",
                    ),
                    (
                        channel_context["other_label"],
                        summarize_channel_list(channel_context["other_channels"]),
                        "bold white",
                    ),
                    ("Check", "Continue only if that separation is intentional.", "bold yellow"),
                ],
            }
        )
    return sections


def _render_config_view(console: Any, config: Dict[str, Any]) -> None:
    if console is None:
        print(yaml.safe_dump(_clone_without_runtime(config), sort_keys=False, allow_unicode=False))
        return
    from ui_renderers import build_config_view

    console.print(
        build_config_view(
            _clone_without_runtime(config),
            title="Effective Config",
            note_lines=_channel_context_note_lines(config),
        )
    )


def _plain_label(text: str) -> str:
    return re.sub(r"\[/?[^\]]+\]", "", str(text))


def _option_is_enabled(label: str) -> bool:
    return "[dim]" not in str(label)


def _confirm_choice(
    console: Any,
    title: str,
    prompt: str,
    *,
    confirm_label: str,
    cancel_label: str = "Back",
) -> bool:
    options = [confirm_label, cancel_label]
    selected = _arrow_select(title, options, console, start_index=1)
    if selected is None:
        if console is None:
            print(title)
            print(prompt)
            print(f"1. {confirm_label}")
            print(f"2. {cancel_label}")
        else:
            console.print(f"[bold cyan]{title}[/bold cyan]")
            console.print(prompt)
            console.print(f"1. {confirm_label}")
            console.print(f"2. {cancel_label}")
        choice = _prompt("Select option (1-2)")
        return choice.strip() == "1"
    return selected == 0


def _render_session_summary(console: Any, config: Dict[str, Any], live_state: LiveState) -> None:
    stim = config["stimulus"]
    setup = _ensure_setup_state(live_state)
    lines = [
        "Preflight Session Summary",
        f"output_directory: {config['output_directory']}",
        f"device_count: {len(config.get('devices', []))}",
        "",
        *_build_launch_contract_lines(config),
        "",
        f"closed_loop.rule_count: {len(get_closed_loop_rules(config))}",
        (
            "stimulus: "
            f"enabled={stim.get('enabled')} mode={stim.get('mode', 'monitor')} "
            f"control_mode={stim.get('control_mode', 'closed_loop')}"
        ),
        (
            "ttl_capture: "
            f"enabled={config.get('ttl_capture', {}).get('enabled')} "
            f"port={config.get('ttl_capture', {}).get('port', '') or '[select in preflight]'}"
        ),
        f"laser_setup: {setup.get('laser_state', 'not_started')}",
        f"teensy_setup: {setup.get('teensy_state', 'not_started')}",
    ]
    if str(stim.get("control_mode", "closed_loop")).lower() == "open_loop":
        start_mode, start_at_text, start_detail = _format_stimulus_start(datetime.now(), stim)
        lines.append(
            "open_loop: "
            f"enabled={stim.get('enabled')} "
            f"run_for_minutes={stim.get('run_for_minutes')} "
            f"start_mode={start_mode} "
            f"scheduled_start={start_at_text} "
            f"start_detail={start_detail} "
            f"target_channels={summarize_channel_list(stim.get('target_channels'))}"
        )
    else:
        lines.append(f"closed_loop_rules: {summarize_closed_loop_rules(config)}")
    warnings = get_stim_warnings(stim)
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"- {msg}" for msg in warnings)
    lines.append(
        "Safety behavior: if stimulation hardware fails to initialize, outputs remain OFF for this run."
    )
    _print_lines(console, lines)


def _render_stim_view(console: Any, config: Dict[str, Any], live_state: LiveState) -> None:
    stim = config["stimulus"]
    square = stim.get("square", {})
    pulse = stim.get("pulse", {})
    train = stim.get("train", {})
    derived = stim.get("derived", {})
    setup = _ensure_setup_state(live_state)
    control_mode = str(stim.get("control_mode", "closed_loop")).lower()

    period_ms = pulse.get("period_ms", square.get("period_ms", 0))
    time_on_ms = pulse.get("time_on_ms", square.get("time_on_ms", 0))
    on_seconds = train.get("on_seconds", stim.get("window_on_seconds", 1.0))
    off_seconds = train.get("off_seconds", 0.0)
    start_mode, start_at_text, start_detail = _format_stimulus_start(datetime.now(), stim)

    wave_summary = build_square_wave_preview(period_ms, time_on_ms)
    pulse_plot = build_square_wave_plot(period_ms, time_on_ms, cycles=5, cols_per_cycle=10)
    train_plot = build_train_envelope_plot(on_seconds, off_seconds, cycles=2, cols_per_cycle=30)

    lines = [
        "[bold]Stimulus Configuration[/bold]",
        "[bold cyan]Run Mode[/bold cyan]",
        f"enabled={stim.get('enabled')}  mode={stim.get('mode', 'monitor')}",
        f"control_mode={control_mode}",
        (
            f"open_loop enabled={stim.get('enabled') and control_mode == 'open_loop'} "
            f"run_for_minutes={stim.get('run_for_minutes')} "
            f"start_mode={start_mode} "
            f"scheduled_start={start_at_text} "
            f"target_channels={summarize_channel_list(stim.get('target_channels')) if control_mode == 'open_loop' else summarize_closed_loop_rule_outputs(config)}"
        ),
        f"start_detail={start_detail}",
        "",
        "[bold cyan]Routing[/bold cyan]",
        f"driver={stim.get('driver')}  port={stim.get('port')}",
        f"dll_path={stim.get('dll_path')}",
        f"channels: {summarize_channels(stim.get('channels', {}))}",
        f"open_loop target_channels: {summarize_channel_list(stim.get('target_channels'))}",
        f"closed_loop rules: {summarize_closed_loop_rules(config)}",
        (
            "ttl_capture: "
            f"enabled={config.get('ttl_capture', {}).get('enabled')} "
            f"port={config.get('ttl_capture', {}).get('port', '') or '[select in preflight]'}"
        ),
        (
            "setup_state: "
            f"laser={setup.get('laser_state', 'not_started')} "
            f"teensy={setup.get('teensy_state', 'not_started')}"
        ),
        "",
        "[bold cyan]Pulse Layer (inside ON epoch)[/bold cyan]",
        f"period_ms={period_ms}  time_on_ms={time_on_ms}",
        *[f"  {row}" for row in pulse_plot],
        f"summary: {wave_summary}",
        "",
        "[bold cyan]Train Layer (ON/OFF envelope)[/bold cyan]",
        f"on_seconds={on_seconds}  off_seconds={off_seconds}",
        *[f"  {row}" for row in train_plot],
        "",
        "[bold cyan]Derived Metrics[/bold cyan]",
        f"pulse_hz={derived.get('pulse_frequency_hz')}  interpulse_ms={derived.get('interpulse_ms')}",
        f"pulse_duty={derived.get('pulse_duty')}  train_duty={derived.get('train_duty')}  effective_duty={derived.get('effective_duty')}",
        f"pulses_per_on_epoch={derived.get('pulses_per_on_epoch')}  pulses_per_cycle={derived.get('pulses_per_cycle')}",
        "",
        "Safety behavior: if stimulation hardware fails to initialize, outputs remain OFF for this run.",
    ]
    _print_lines(console, lines)


def _print_lines(console: Any, lines: List[str]) -> None:
    if console is None:
        for line in lines:
            print(line)
        return

    from rich.panel import Panel

    content = "\n".join(lines)
    console.print(Panel.fit(content, border_style="cyan"))


def _prompt(text: str) -> str:
    return input(f"{text} ").strip()


def _normalize_animal_ids(text: Any) -> List[str]:
    if isinstance(text, list):
        items = text
    elif text is None:
        items = []
    else:
        items = str(text).split(",")
    normalized: List[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item).strip().upper()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _format_animal_ids(ids: List[str]) -> str:
    return ", ".join(ids) if ids else "(none)"


def _format_detected_animal_ids(ids: List[str]) -> str:
    if not ids:
        return "(none)"
    return ", ".join(f"[bold green]{animal_id}[/bold green]" for animal_id in ids)


def _closed_loop_unassigned_rule_ids(config: Dict[str, Any]) -> List[str]:
    if str(config.get("stimulus", {}).get("control_mode", "closed_loop")).lower() != "closed_loop":
        return []
    missing: List[str] = []
    for rule in get_closed_loop_rules(config):
        assigned = _normalize_animal_ids(rule.get("assigned_animal_ids", []))
        if not assigned:
            missing.append(str(rule.get("id", "?")))
    return missing


def _show_closed_loop_assignment_block(console: Any, missing_rule_ids: List[str]) -> None:
    if not missing_rule_ids:
        return
    lines = [
        "Closed-loop launch blocked: every rule must have at least one assigned RFID.",
        f"Missing assignment for rule(s): {', '.join(missing_rule_ids)}",
        "Retry RFID scan or enter the RFID manually before launch.",
    ]
    if console is None:
        for line in lines:
            print(line)
        return
    from rich.panel import Panel

    console.print(
        Panel.fit(
            "\n".join(lines),
            title="[bold red]Closed-Loop Assignment Required[/bold red]",
            border_style="red",
        )
    )


def _render_closed_loop_rule_assignment_panel(
    console: Any,
    rule: Dict[str, Any],
    known_ids: List[str],
    current_ids: List[str],
) -> List[str]:
    lines = [
        f"[bold cyan]Rule[/bold cyan] {rule.get('id', '?')}",
        f"[bold]Devices:[/bold] {summarize_channel_list(rule.get('devices'))}",
        f"[bold]Outputs:[/bold] {summarize_channel_list(rule.get('outputs', {}).get('laser_channels'))}",
        f"[bold]Classifier:[/bold] {_format_classifier_label(str(rule.get('classifier', {}).get('plugin') or ''))}",
        f"[bold]Current Assignment:[/bold] {_format_animal_ids(current_ids)}",
    ]
    if known_ids:
        lines.append(f"[bold]Detected RFID candidates:[/bold] {_format_detected_animal_ids(known_ids)}")
    else:
        lines.append("[bold]Detected RFID candidates:[/bold] [yellow](none available yet)[/yellow]")
        lines.append("You can enter RFIDs manually for this rule.")

    if console is None:
        print("=== Closed-Loop Rule Assignment ===")
        for line in lines:
            print(_plain_label(line))
        return lines

    from rich.panel import Panel

    console.print(Panel.fit("\n".join(lines), border_style="cyan", title="[bold cyan]Closed-Loop Rule Assignment[/bold cyan]"))
    return lines


def _build_device_lookup(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for idx, entry in enumerate(config.get("devices", [])):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or f"Reader-{idx + 1}").strip()
        host = str(entry.get("host") or "").strip()
        if name:
            lookup[name] = entry
        if host:
            lookup[host] = entry
    return lookup


def _discover_device_rfid_candidates(
    config: Dict[str, Any],
    required_tokens: List[str],
    console: Any = None,
    *,
    sample_seconds: float = 5.0,
) -> Dict[str, List[str]]:
    parser = DataParser()
    device_lookup = _build_device_lookup(config)
    results: Dict[str, List[str]] = {}
    for token in required_tokens:
        device_cfg = device_lookup.get(token)
        if not isinstance(device_cfg, dict):
            results[token] = []
            continue
        host = str(device_cfg.get("host") or "").strip()
        port = int(device_cfg.get("port") or 0)
        canonical_name = str(device_cfg.get("name") or token).strip() or token
        if console is None:
            print(f"Scanning RFIDs on {canonical_name} ({host}:{port}) for {sample_seconds:g}s...")
        else:
            console.print(
                f"Scanning RFIDs on [bold white]{canonical_name}[/bold white] "
                f"([bold white]{host}:{port}[/bold white]) for [bold white]{sample_seconds:g}s[/bold white]..."
            )
        seen: List[str] = []
        seen_set: set[str] = set()
        try:
            with socket.create_connection((host, port), timeout=float(config.get("network", {}).get("socket_timeout_seconds", 5.0))) as conn:
                conn.settimeout(0.25)
                conn.sendall(b"RRLOOP\r\n")
                deadline = time.monotonic() + sample_seconds
                pending = ""
                while time.monotonic() < deadline:
                    try:
                        chunk = conn.recv(1024)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    decoded = pending + chunk.decode("ascii", errors="replace")
                    readings = parser.parse(decoded, datetime.now())
                    pending = decoded[-256:]
                    for reading in readings:
                        animal_id = str(reading.animal_id).strip().upper()
                        if not animal_id or animal_id in seen_set:
                            continue
                        seen_set.add(animal_id)
                        seen.append(animal_id)
        except Exception as exc:
            if console is None:
                print(f"RFID scan failed on {canonical_name}: {exc}")
            else:
                console.print(
                    f"[yellow]RFID scan failed on [bold white]{canonical_name}[/bold white]: {exc}[/yellow]"
                )
        results[token] = seen
        if canonical_name != token:
            results[canonical_name] = list(seen)
    return results


def _discover_rule_rfid_candidates(
    config: Dict[str, Any],
    console: Any = None,
    *,
    sample_seconds: float = 3.0,
) -> Dict[str, List[str]]:
    required_tokens: List[str] = []
    for rule in get_closed_loop_rules(config):
        for token in rule.get("devices", []):
            text = str(token).strip()
            if text and text not in required_tokens:
                required_tokens.append(text)
    return _discover_device_rfid_candidates(
        config,
        required_tokens,
        console=console,
        sample_seconds=sample_seconds,
    )


def _choose_closed_loop_rfid_subset(
    console: Any,
    known_ids: List[str],
    current_ids: List[str],
) -> List[str]:
    if not known_ids:
        if console is None:
            print("No known RFID candidates are available for this rule.")
            entered = _prompt("Enter RFID(s), comma-separated, or leave blank to clear")
        else:
            console.print("[yellow]No known RFID candidates are available for this rule.[/yellow]")
            entered = _prompt("Enter RFID(s), comma-separated, or leave blank to clear")
        return _normalize_animal_ids(entered)

    if console is None:
        print("Known RFID candidates:")
        for idx, animal_id in enumerate(known_ids, start=1):
            print(f"{idx}. {animal_id}")
        entered = _prompt("Enter candidate numbers separated by commas, blank selects all listed")
    else:
        from rich.table import Table

        table = Table(show_header=False, box=None, expand=False, pad_edge=False)
        table.add_column("Index", style="bold cyan", width=8)
        table.add_column("RFID", style="bold white")
        for idx, animal_id in enumerate(known_ids, start=1):
            table.add_row(str(idx), animal_id)
        console.print(table)
        entered = _prompt("Enter candidate numbers separated by commas, blank selects all listed")

    chosen_indices = _normalize_animal_ids(entered)
    if not chosen_indices:
        return list(known_ids)

    selected: List[str] = []
    for token in chosen_indices:
        try:
            index = int(token)
        except Exception:
            continue
        if 1 <= index <= len(known_ids):
            value = known_ids[index - 1]
            if value not in selected:
                selected.append(value)

    if selected:
        return selected

    if console is None:
        print("No valid candidate numbers entered; keeping current assignment.")
    else:
        console.print("[yellow]No valid candidate numbers entered; keeping current assignment.[/yellow]")
    return list(current_ids)


def _prompt_closed_loop_assignments(config: Dict[str, Any], console: Any = None) -> Dict[str, Any]:
    if str(config.get("stimulus", {}).get("control_mode", "closed_loop")).lower() != "closed_loop":
        return config
    rules = get_closed_loop_rules(config)
    if not rules:
        return config

    updated = _clone_without_runtime(config)
    updated_rules = get_closed_loop_rules(updated)
    if console is None:
        print("=== Closed-Loop Rule Assignments ===")
        print("Assign RFIDs to each rule for this session.")
        print("The program will scan configured readers first and populate per-rule RFID choices.")
    else:
        console.print("[bold cyan]Closed-Loop Rule Assignments[/bold cyan]")
        console.print("Assign RFIDs to each rule for this session.")
        console.print("The program will scan configured readers first and populate per-rule RFID choices.")

    discovery_by_device = _discover_rule_rfid_candidates(config, console=console)

    for idx, rule in enumerate(updated_rules):
        current_ids = _normalize_animal_ids(rule.get("assigned_animal_ids", []))
        while True:
            known_ids: List[str] = []
            seen_known: set[str] = set()
            rule_tokens = [str(token).strip() for token in rule.get("devices", []) if str(token).strip()]
            for token in rule_tokens:
                for animal_id in discovery_by_device.get(token, []):
                    if animal_id in seen_known:
                        continue
                    seen_known.add(animal_id)
                    known_ids.append(animal_id)
            if console is None:
                print("")
            else:
                console.print("")
            context_lines = _render_closed_loop_rule_assignment_panel(console, rule, known_ids, current_ids)
            if len(known_ids) == 1:
                options = [
                    f"Accept detected RFID {known_ids[0]}",
                    "Retry RFID scan",
                    "Enter RFID(s) manually",
                    "Cancel launch",
                ]
            elif known_ids:
                options = [
                    "Keep current assignment" if current_ids else "[dim]Keep current assignment[/dim]",
                    "Choose from discovered RFID candidates",
                    "Retry RFID scan",
                    "Enter RFID(s) manually",
                    "Cancel launch",
                ]
            else:
                options = [
                    "Keep current assignment" if current_ids else "[dim]Keep current assignment[/dim]",
                    "Retry RFID scan",
                    "Enter RFID(s) manually",
                    "Cancel launch",
                ]
            choice = _select_from_labels(
                console,
                f"Assign RFIDs for rule {rule.get('id', '?')}",
                options,
                context_lines=context_lines,
            )
            if choice is None:
                updated_rules[idx]["assigned_animal_ids"] = current_ids
                break
            if len(known_ids) == 1:
                if choice == 0:
                    updated_rules[idx]["assigned_animal_ids"] = list(known_ids)
                    break
                if choice == 1:
                    discovery_by_device.update(_discover_device_rfid_candidates(config, rule_tokens, console=console))
                    continue
                if choice == 2:
                    entered = _prompt("Enter RFID(s), comma-separated; blank leaves this rule unassigned and blocks launch")
                    updated_rules[idx]["assigned_animal_ids"] = _normalize_animal_ids(entered)
                    break
                updated_rules[idx]["assigned_animal_ids"] = current_ids
                break
            if known_ids:
                if choice == 0:
                    updated_rules[idx]["assigned_animal_ids"] = current_ids
                    break
                if choice == 1:
                    updated_rules[idx]["assigned_animal_ids"] = _choose_closed_loop_rfid_subset(console, known_ids, current_ids)
                    break
                if choice == 2:
                    discovery_by_device.update(_discover_device_rfid_candidates(config, rule_tokens, console=console))
                    continue
                if choice == 3:
                    entered = _prompt("Enter RFID(s), comma-separated; blank leaves this rule unassigned and blocks launch")
                    updated_rules[idx]["assigned_animal_ids"] = _normalize_animal_ids(entered)
                    break
                updated_rules[idx]["assigned_animal_ids"] = current_ids
                break
            if choice == 0:
                updated_rules[idx]["assigned_animal_ids"] = current_ids
                break
            if choice == 1:
                discovery_by_device.update(_discover_device_rfid_candidates(config, rule_tokens, console=console))
                continue
            if choice == 2:
                entered = _prompt("Enter RFID(s), comma-separated; blank leaves this rule unassigned and blocks launch")
                updated_rules[idx]["assigned_animal_ids"] = _normalize_animal_ids(entered)
                break
            updated_rules[idx]["assigned_animal_ids"] = current_ids
            break
    return updated


def _render_open_loop_assignment_panel(
    console: Any,
    assignment: Dict[str, Any],
    known_ids: List[str],
    current_ids: List[str],
) -> List[str]:
    lines = [
        f"[bold cyan]Assignment[/bold cyan] {assignment.get('id', '?')}",
        f"[bold]Device:[/bold] {assignment.get('device', '?')}",
        f"[bold]Channel:[/bold] {assignment.get('channel', '?')}",
        f"[bold]Current Assignment:[/bold] {_format_animal_ids(current_ids)}",
    ]
    if known_ids:
        lines.append(f"[bold]Detected RFID candidates:[/bold] {_format_detected_animal_ids(known_ids)}")
    else:
        lines.append("[bold]Detected RFID candidates:[/bold] [yellow](none available yet)[/yellow]")
        lines.append("You can enter RFIDs manually for this assignment.")

    if console is None:
        print("=== Open-Loop Assignment ===")
        for line in lines:
            print(_plain_label(line))
    return lines


def _prompt_open_loop_assignments(config: Dict[str, Any], console: Any = None) -> Dict[str, Any]:
    if str(config.get("stimulus", {}).get("control_mode", "closed_loop")).lower() != "open_loop":
        return config
    assignments = get_open_loop_assignments(config)
    if not assignments:
        return config

    updated = _clone_without_runtime(config)
    updated_assignments = get_open_loop_assignments(updated)
    if console is None:
        print("=== Open-Loop Assignments ===")
        print("Assign RFIDs to each open-loop output for this session.")
        print("The program will scan matching configured readers first when possible.")
    else:
        console.print("[bold cyan]Open-Loop Assignments[/bold cyan]")
        console.print("Assign RFIDs to each open-loop output for this session.")
        console.print("The program will scan matching configured readers first when possible.")

    required_tokens: List[str] = []
    for assignment in assignments:
        token = str(assignment.get("device") or "").strip()
        if token and token not in required_tokens:
            required_tokens.append(token)
    discovery_by_device = _discover_device_rfid_candidates(config, required_tokens, console=console)

    for idx, assignment in enumerate(updated_assignments):
        current_ids = _normalize_animal_ids(assignment.get("assigned_animal_ids", []))
        lookup_token = str(assignment.get("device") or "").strip()
        while True:
            known_ids = list(discovery_by_device.get(lookup_token, []))
            if console is None:
                print("")
            else:
                console.print("")
            context_lines = _render_open_loop_assignment_panel(console, assignment, known_ids, current_ids)
            if len(known_ids) == 1:
                options = [
                    f"Accept detected RFID {known_ids[0]}",
                    "Retry RFID scan",
                    "Enter RFID(s) manually",
                    "Clear assignment",
                ]
            elif known_ids:
                options = [
                    "Keep current assignment" if current_ids else "Leave unassigned",
                    "Choose from discovered RFID candidates",
                    "Retry RFID scan",
                    "Clear assignment",
                ]
            else:
                options = [
                    "Keep current assignment" if current_ids else "Leave unassigned",
                    "Retry RFID scan",
                    "Enter RFID(s) manually",
                    "Clear assignment",
                ]
            choice = _select_from_labels(
                console,
                f"Assign RFIDs for open-loop slot {assignment.get('id', '?')}",
                options,
                context_lines=context_lines,
            )
            if choice is None or choice == 0:
                updated_assignments[idx]["assigned_animal_ids"] = list(known_ids) if len(known_ids) == 1 and choice == 0 else current_ids
                break
            if len(known_ids) == 1 and choice == 1:
                discovery_by_device.update(
                    _discover_device_rfid_candidates(config, [lookup_token], console=console)
                )
                continue
            if len(known_ids) == 1 and choice == 2:
                entered = _prompt("Enter RFID(s), comma-separated, blank to clear")
                updated_assignments[idx]["assigned_animal_ids"] = _normalize_animal_ids(entered)
                break
            if len(known_ids) == 1:
                updated_assignments[idx]["assigned_animal_ids"] = []
                break
            if known_ids and choice == 1:
                updated_assignments[idx]["assigned_animal_ids"] = _choose_closed_loop_rfid_subset(console, known_ids, current_ids)
                break
            if known_ids and choice == 2:
                discovery_by_device.update(
                    _discover_device_rfid_candidates(config, [lookup_token], console=console)
                )
                continue
            if not known_ids and choice == 1:
                discovery_by_device.update(
                    _discover_device_rfid_candidates(config, [lookup_token], console=console)
                )
                continue
            if not known_ids and choice == 2:
                entered = _prompt("Enter RFID(s), comma-separated, blank to clear")
                updated_assignments[idx]["assigned_animal_ids"] = _normalize_animal_ids(entered)
                break
            updated_assignments[idx]["assigned_animal_ids"] = []
            break
    return updated


def _prompt_session_label(config: Dict[str, Any], live_state: LiveState, console: Any = None) -> bool:
    while True:
        current_label = live_state.session_label or ""
        prompt_text = "Enter session label for this run (blank keeps timestamp-only folder naming)."
        if console is None:
            print("=== Session Label ===")
            print(prompt_text)
            print(f"Current value: {current_label or '[blank]'}")
        else:
            console.print("[bold cyan]Session Label[/bold cyan]")
            console.print(prompt_text)
            console.print(f"Current value: {current_label or '[blank]'}")
        entered = _prompt("Session label")
        if entered == "" and current_label:
            entered = current_label
        preview = build_session_folder_name(datetime.now(), entered)
        preview_text = f"{config['output_directory']}/{preview}"
        confirm_title = f"Session Label [bold green]{preview_text}[/bold green]"
        approved = _confirm_choice(
            console,
            confirm_title,
            "Use this session label and continue to launch confirmation?",
            confirm_label="Use This Label",
            cancel_label="Edit Label",
        )
        if approved:
            live_state.session_label = entered or None
            return True


def _pause_return_to_menu(console: Any = None) -> None:
    if console is None:
        input("Press Enter to return to menu...")
    else:
        console.print("Press Enter to return to menu...")
        input()


def _prompt_field_value(field: EditField) -> str:
    if field.kind == "bool":
        return _prompt(f"Enter new value for {field.label} (true/false)")
    if field.kind == "int":
        return _prompt(f"Enter new value for {field.label} (integer)")
    if field.kind == "float":
        return _prompt(f"Enter new value for {field.label} (number)")
    return _prompt(f"Enter new value for {field.label}")


def _supports_arrow_select() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _read_nav_key_windows() -> tuple[str, str]:
    import msvcrt

    ch = msvcrt.getch()
    if ch in (b"\r", b"\n"):
        return "enter", ""
    if ch in (b"\x00", b"\xe0"):
        ch2 = msvcrt.getch()
        if ch2 == b"H":
            return "up", ""
        if ch2 == b"P":
            return "down", ""
        return "other", ""
    if ch.isdigit():
        return "digit", ch.decode("ascii", errors="ignore")
    return "other", ""


def _read_nav_key_posix() -> tuple[str, str]:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    try:
        ch = sys.stdin.read(1)
        if ch in ("\r", "\n"):
            return "enter", ""
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            if seq == "[A":
                return "up", ""
            if seq == "[B":
                return "down", ""
            return "other", ""
        if ch.isdigit():
            return "digit", ch
        return "other", ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _arrow_select(
    title: str,
    options: List[str],
    console: Any,
    start_index: int = 0,
    *,
    context_lines: Optional[List[str]] = None,
    status_config: Optional[Dict[str, Any]] = None,
    live_state: Optional[LiveState] = None,
) -> Optional[int]:
    if console is None or not options or not _supports_arrow_select():
        return None

    enabled_indices = [i for i, label in enumerate(options) if _option_is_enabled(label)]
    if not enabled_indices:
        return None

    idx = max(0, min(start_index, len(options) - 1))
    if idx not in enabled_indices:
        idx = enabled_indices[0]
    typed_digits = ""
    while True:
        _wait_for_terminal_size(console)
        console.clear()
        if title == "Preflight Main Menu":
            _render_preflight_menu(console, options, idx, status_config, live_state)
        else:
            lines = [f"[bold cyan]{title}[/bold cyan]", "[dim]Use Up/Down arrows and Enter. Or type number.[/dim]"]
            if context_lines:
                lines.extend(context_lines)
                lines.append("")
            for i, label in enumerate(options):
                if i == idx:
                    lines.append(f"[bold white]> {i + 1}. {label}[/bold white]")
                else:
                    lines.append(f"[white]  {i + 1}. {label}[/white]")
            _print_lines(console, lines)

        key, payload = _read_nav_key_windows() if os.name == "nt" else _read_nav_key_posix()
        if key == "up":
            current_pos = enabled_indices.index(idx)
            idx = enabled_indices[(current_pos - 1) % len(enabled_indices)]
            typed_digits = ""
            continue
        if key == "down":
            current_pos = enabled_indices.index(idx)
            idx = enabled_indices[(current_pos + 1) % len(enabled_indices)]
            typed_digits = ""
            continue
        if key == "enter":
            return idx
        if key == "digit":
            typed_digits += payload
            try:
                numeric = int(typed_digits)
            except Exception:
                continue
            if 1 <= numeric <= len(options) and (numeric - 1) in enabled_indices:
                return numeric - 1
            continue


def _run_quick_edit(
    console: Any,
    config: Dict[str, Any],
    validator: Optional[ValidatorFn],
    live_state: LiveState,
) -> Dict[str, Any]:
    cursor = 0
    while True:
        option_labels = [f"{field.label} = {_get_path(config, field.path)}" for field in EDITABLE_FIELDS]
        option_labels.append("Back")
        selected = _arrow_select("Stim Quick Editor", option_labels, console, start_index=cursor)
        if selected is None:
            if console is None:
                print("\nStim Quick Editor")
            else:
                console.print("\n[bold cyan]Stim Quick Editor[/bold cyan]")

            for idx, edit_field in enumerate(EDITABLE_FIELDS, start=1):
                current = _get_path(config, edit_field.path)
                print(f"{idx}. {edit_field.label} = {current}")
            print("0. Back")

            choice = _prompt("Select field number")
            if choice == "0":
                return config
            try:
                selected = int(choice) - 1
            except Exception:
                if console is None:
                    print("Invalid selection. Choose one of the listed numbers.")
                else:
                    console.print("[bold red]Invalid selection.[/bold red] Choose one of the listed numbers.")
                continue

        if selected == len(option_labels) - 1:
            return config
        if selected < 0 or selected >= len(EDITABLE_FIELDS):
            if console is None:
                print("Invalid selection. Choose one of the listed numbers.")
            else:
                console.print("[bold red]Invalid selection.[/bold red] Choose one of the listed numbers.")
            continue

        cursor = selected
        field = EDITABLE_FIELDS[selected]
        new_raw = _prompt_field_value(field)

        try:
            parsed = _parse_field_value(field, new_raw)
            candidate = _clone_without_runtime(config)
            _set_path(candidate, field.path, parsed)
            validated = _apply_validation(candidate, validator)
        except ValueError as exc:
            if console is None:
                print(f"Edit rejected: {exc}")
            else:
                console.print(f"[bold red]Edit rejected:[/bold red] {exc}")
            continue

        config = validated
        _reset_setup_state(live_state)
        if console is None:
            print("Value updated and configuration validated. Hardware setup selections were reset.")
        else:
            console.print(
                "[bold green]Value updated and configuration validated. Hardware setup selections were reset.[/bold green]"
            )


def _run_validation_action(
    config: Dict[str, Any],
    validator: Optional[ValidatorFn],
    console: Any = None,
) -> Dict[str, Any]:
    output_capture = io.StringIO()
    try:
        with redirect_stdout(output_capture):
            validated = _apply_validation(_clone_without_runtime(config), validator)
    except ValueError as exc:
        warnings = _extract_warning_lines(output_capture.getvalue())
        if warnings:
            explained = [_explain_warning(line, config) for line in warnings]
            _print_warning_block(console, explained, title="Validation Warnings")
        if console is None:
            print(f"Validation failed: {exc}")
        else:
            console.print(f"[bold red]Validation failed:[/bold red] {exc}")
        return config
    warnings = _extract_warning_lines(output_capture.getvalue())
    if warnings:
        explained = [_explain_warning(line, config) for line in warnings]
        _print_warning_block(console, explained, title="Validation Warnings")
    if console is None:
        print("Config Validation Passed: no parameter mismatch found.")
        print("")
    else:
        console.print("[bold green]Config Validation Passed:[/bold green] no parameter mismatch found.")
        console.print("")
    return validated


def _extract_warning_lines(stdout_text: str) -> List[str]:
    lines: List[str] = []
    for raw in stdout_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Warning:"):
            lines.append(line)
    return lines


def _explain_warning(warning_line: str, config: Dict[str, Any]) -> str:
    if "triggers.clf_window_seconds" in warning_line and "effective window" in warning_line:
        avg = config.get("data", {}).get("averaging_window_seconds")
        win = config.get("triggers", {}).get("clf_window_seconds")
        return (
            f"{warning_line} -> Effective classifier window is {avg}s (configured {win}s). "
            "Match these values if you want unambiguous behavior."
        )
    if "closed_loop.rules." in warning_line and "effective window" in warning_line:
        avg = config.get("data", {}).get("averaging_window_seconds")
        return (
            f"{warning_line} -> Effective classifier window is {avg}s. "
            "Match these values if you want unambiguous behavior."
        )
    return warning_line


def _print_warning_block(console: Any, warnings: List[str], title: str = "Warnings") -> None:
    if not warnings:
        return
    if console is None:
        print("")
        print(f"!!! {title} !!!")
        for warning in warnings:
            print(f" - {warning}")
        print("")
        return

    from rich.panel import Panel

    content = "\n".join(f"- {line}" for line in warnings)
    console.print(Panel.fit(content, title=f"[bold yellow]{title}[/bold yellow]", border_style="yellow"))


def _status_line(label: str, state: str, detail: str) -> str:
    state_upper = state.upper()
    if state_upper == "OK":
        state_fmt = "[bold green]OK[/bold green]"
        detail_fmt = detail
    elif state_upper == "FAILED":
        state_fmt = "[bold red]FAILED[/bold red]"
        detail_fmt = f"[#ff7f7f]{detail}[/#ff7f7f]"
    elif state_upper in ("DISABLED", "INACTIVE"):
        state_fmt = "[#ff7f7f]DISABLED[/#ff7f7f]"
        detail_fmt = f"[#ff7f7f]{detail}[/#ff7f7f]"
    elif state_upper == "MISSING":
        state_fmt = "[yellow]MISSING[/yellow]"
        detail_fmt = detail
    else:
        state_fmt = f"[yellow]{state_upper}[/yellow]"
        detail_fmt = detail
    return f"{label}: [{state_fmt}] {detail_fmt}"


def _build_preflight_status_items(config: Dict[str, Any], live_state: LiveState) -> List[Tuple[str, str, str]]:
    stim = config.get("stimulus", {})
    ttl_cfg = config.get("ttl_capture", {})
    setup = _ensure_setup_state(live_state)
    laser_required = _laser_setup_required(config)
    teensy_required = _teensy_setup_required(config)

    if laser_required:
        doric_id = stim.get("uid") or stim.get("port")
        doric_state = "OK" if doric_id else ("FAILED" if setup.get("laser_state") == "failed" else "PENDING")
        if stim.get("uid"):
            doric_detail = f"uid={stim.get('uid')}"
        elif stim.get("port"):
            doric_detail = f"port={stim.get('port')}"
        else:
            doric_detail = "not selected"
    else:
        doric_state = "INACTIVE"
        doric_detail = "not required"

    if teensy_required:
        teensy_state = "OK" if setup.get("teensy_ready") else ("FAILED" if setup.get("teensy_state") == "failed" else "PENDING")
        teensy_detail = ttl_cfg.get("port") or "[select in preflight]"
    else:
        teensy_state = "INACTIVE" if not ttl_cfg.get("enabled") else "OK"
        teensy_detail = ttl_cfg.get("port") or "not required"

    bench_raw = str(setup.get("bench_state", "not_tested")).lower()
    if bench_raw == "passed":
        bench_state = "OK"
    elif bench_raw == "failed":
        bench_state = "FAILED"
    elif bench_raw == "running":
        bench_state = "PENDING"
    else:
        bench_state = "MISSING"
    bench_detail = setup.get("bench_detail") or "not tested"

    launch_blockers: List[str] = []
    if laser_required and not (stim.get("uid") or stim.get("port")):
        launch_blockers.append("find Doric")
    if teensy_required and not setup.get("teensy_ready"):
        launch_blockers.append("setup Teensy")
    if not laser_required:
        launch_state = "OK"
        launch_detail = "no laser required in config"
    elif not launch_blockers:
        launch_state = "OK"
        launch_detail = "ready"
    else:
        launch_state = "PENDING"
        launch_detail = ", ".join(launch_blockers)

    return [
        ("Find Doric Device", doric_state, doric_detail),
        ("Setup Teensy", teensy_state, teensy_detail),
        ("Test Laser w/o Animals", bench_state, bench_detail),
        ("Launch Experiment", launch_state, launch_detail),
    ]


def _render_preflight_menu(
    console: Any,
    options: List[str],
    idx: int,
    config: Optional[Dict[str, Any]] = None,
    live_state: Optional[LiveState] = None,
) -> None:
    if console is None:
        return

    if config is None or live_state is None:
        from rich.panel import Panel
        menu_lines = ["[dim]Use Up/Down arrows and Enter. Or type number.[/dim]"]
        for i, label in enumerate(options):
            if i == idx:
                menu_lines.append(f"[bold white]> {i + 1}. {label}[/bold white]")
            else:
                menu_lines.append(f"[white]  {i + 1}. {label}[/white]")
        console.print(Panel("\n".join(menu_lines), title="[bold cyan]Preflight Main Menu[/bold cyan]", border_style="cyan"))
        return

    from ui_renderers import MenuOption, StatusItem, build_preflight_dashboard

    console.print(
        build_preflight_dashboard(
            menu_options=[
                MenuOption(label=_plain_label(label), enabled="[dim]" not in str(label))
                for label in options
            ],
            selected_index=idx,
            status_items=[StatusItem(label=label, state=state, detail=detail) for label, state, detail in _build_preflight_status_items(config, live_state)],
            summary_sections=_build_session_summary_sections(config, live_state),
        )
    )

def _build_preflight_menu_options(config: Dict[str, Any], live_state: LiveState) -> List[str]:
    teensy_required = _teensy_setup_required(config)
    setup = _ensure_setup_state(live_state)
    stim = config.get("stimulus", {})
    has_doric_identity = bool(stim.get("uid") or stim.get("port"))
    bench_ready = has_doric_identity and (not teensy_required or setup.get("teensy_ready"))
    bench_label = "Test Laser Program w/o Animals" if bench_ready else "[dim]Test Laser Program w/o Animals[/dim]"
    teensy_label = "Setup Teensy"
    if not teensy_required:
        teensy_label = "[dim]Setup Teensy[/dim]"
    return [
        "Find Doric Device",
        teensy_label,
        bench_label,
        "[bold red]Launch Experiment[/bold red]",
        "Show Config",
        "[bold]Exit[/bold]",
    ]


def _build_launch_checklist(config: Dict[str, Any], live_state: LiveState) -> List[str]:
    devices = config.get("devices", [])
    stim = config.get("stimulus", {})
    ttl_cfg = config.get("ttl_capture", {})
    triggers = config.get("triggers", {})
    setup = _ensure_setup_state(live_state)
    control_mode = str(stim.get("control_mode", "closed_loop")).lower()
    valid_devices = 0
    configured_endpoints: List[Tuple[str, int]] = []
    for d in devices:
        if isinstance(d, dict) and d.get("host") and d.get("port"):
            valid_devices += 1
            try:
                configured_endpoints.append((str(d.get("host")), int(d.get("port"))))
            except Exception:
                pass

    connected_count = 0
    unreachable_count = 0
    probe_notes: List[str] = []
    for host, port in configured_endpoints:
        try:
            with socket.create_connection((host, port), timeout=0.75):
                connected_count += 1
        except Exception as exc:
            unreachable_count += 1
            if len(probe_notes) < 3:
                probe_notes.append(f"{host}:{port} -> {exc}")
    lines = [
        _status_line("TCP config", "CONFIGURED" if valid_devices > 0 else "MISSING", f"{valid_devices} configured device(s)"),
        _status_line(
            "TCP connectivity",
            "OK" if (valid_devices > 0 and connected_count == valid_devices) else ("FAILED" if valid_devices > 0 else "MISSING"),
            f"connected={connected_count} unreachable={unreachable_count}",
        ),
    ]
    if probe_notes:
        lines.append("TCP probe details:")
        lines.extend([f"- {note}" for note in probe_notes])

    stim_enabled = bool(stim.get("enabled", False))
    stim_mode = str(stim.get("mode", "monitor")).lower()
    laser_required = _laser_setup_required(config)
    teensy_required = _teensy_setup_required(config)
    if laser_required:
        laser_state = "OK" if setup.get("laser_ready") else ("FAILED" if setup.get("laser_state") == "failed" else "PENDING")
    else:
        laser_state = "OK" if stim_enabled else "INACTIVE"
    lines.append(
        _status_line(
            "Laser controller state",
            laser_state,
            (
                f"enabled={stim_enabled} mode={stim_mode} "
                f"control_mode={stim.get('control_mode', 'closed_loop')}"
            ),
        )
    )
    if laser_required:
        lines.append(
            "Find Doric Device required before launch: select the live Doric port first; [yellow]final verification/arming happens once at launch[/yellow]."
        )
        if setup.get("laser_selected_label"):
            lines.append(f"Selected laser: {setup.get('laser_selected_label')}")
        if setup.get("laser_ready"):
            lines.append("Laser ready for launch: Doric should stay armed and displayed current should match the config.")

    if teensy_required:
        teensy_state = "OK" if setup.get("teensy_ready") else ("FAILED" if setup.get("teensy_state") == "failed" else "PENDING")
    else:
        teensy_state = "OK" if ttl_cfg.get("enabled") else "INACTIVE"
    teensy_detail = (
        f"enabled={ttl_cfg.get('enabled')} port={ttl_cfg.get('port') or '[select in preflight]'}"
    )
    lines.append(_status_line("Teensy TTL ingest", teensy_state, teensy_detail))
    if teensy_required:
        lines.append("Setup Teensy required before launch and before bench tests: select serial device and validate handshake.")
        if setup.get("teensy_selected_label"):
            lines.append(f"Selected Teensy: {setup.get('teensy_selected_label')}")

    if control_mode == "open_loop":
        lines.append(
            "Launch check: "
            f"laser will stimulate [bold white]{summarize_channel_list(stim.get('target_channels'))}[/bold white]"
        )
        lines.append("Temperature-dependent triggering: [bold yellow]not active[/bold yellow]" if not get_closed_loop_rules(config) else "Temperature-dependent triggering: [bold green]active[/bold green]")
        trigger_channels = summarize_closed_loop_rule_outputs(config)
        if trigger_channels != "(none)":
            lines.append(f"Other configured trigger channels: [bold white]{trigger_channels}[/bold white]")
        if _channel_context(config)["show_note"]:
            lines.append("[yellow]Check this before launch:[/yellow]")
            lines.append(
                f"If you expected temperature-based stimulation on [bold white]{trigger_channels}[/bold white], this run is not configured that way."
            )
        lines.append(
            "Launch plan: mode=open_loop "
            f"target_channels={summarize_channel_list(stim.get('target_channels'))} "
            f"run_for_minutes={stim.get('run_for_minutes')} "
            f"start_mode={_format_stimulus_start(datetime.now(), stim)[0]} "
            f"scheduled_start={_format_stimulus_start(datetime.now(), stim)[1]}"
        )
    else:
        active_trigger_channels = summarize_closed_loop_rule_outputs(config)
        lines.append(
            "Launch check: "
            f"laser will stimulate on trigger decisions via [bold white]{active_trigger_channels}[/bold white]"
        )
        lines.append("Temperature-dependent triggering: [bold green]active[/bold green]" if get_closed_loop_rules(config) else "Temperature-dependent triggering: [bold yellow]not active[/bold yellow]")
        open_loop_channels = summarize_channel_list(stim.get("target_channels"))
        if open_loop_channels != "(none)":
            lines.append(f"Other configured open-loop channels: [bold white]{open_loop_channels}[/bold white]")
        if _channel_context(config)["show_note"]:
            lines.append("[yellow]Check this before launch:[/yellow]")
            lines.append(
                f"If you expected the fixed open-loop plan on [bold white]{open_loop_channels}[/bold white], this run is not configured that way."
            )
        lines.append(
            "Launch plan: mode=closed_loop "
            f"rules={summarize_closed_loop_rules(config)}"
        )
        if not stim_enabled or stim_mode == "monitor":
            lines.append(
                "[#ff7f7f]Closed-loop classifier will run in monitor-only mode (laser output disabled).[/#ff7f7f]"
            )
    return lines


def _show_pending_setup(console: Any, title: str, lines: List[str]) -> None:
    _print_lines(console, [f"[bold]{title}[/bold]", *lines])


def _show_caution_setup(console: Any, title: str, lines: List[str]) -> None:
    if console is None:
        print("")
        print(f"!!! {title} !!!")
        for line in lines:
            print(f" - {line}")
        print("")
        return

    from rich.panel import Panel

    content = "\n".join(f"- {line}" for line in lines)
    console.print(
        Panel.fit(
            content,
            title=f"[bold yellow]{title}[/bold yellow]",
            border_style="yellow",
        )
    )


def _ensure_setup_state(live_state: LiveState) -> Dict[str, Any]:
    if not live_state.setup:
        live_state.setup = default_setup_state()
    for key, value in default_setup_state().items():
        live_state.setup.setdefault(key, value)
    return live_state.setup


def _reset_setup_state(live_state: LiveState) -> None:
    state = _ensure_setup_state(live_state)
    laser_driver = live_state.laser_driver
    if laser_driver is not None:
        try:
            laser_driver.close()
        except Exception:
            pass
    state["laser_state"] = "not_started"
    state["laser_ready"] = False
    state["laser_selected_label"] = ""
    state["laser_last_error"] = ""
    live_state.laser_driver = None
    state["bench_state"] = "not_tested"
    state["bench_detail"] = ""
    state["teensy_state"] = "not_started"
    state["teensy_ready"] = False
    state["teensy_selected_label"] = ""
    state["teensy_last_error"] = ""


def _select_from_labels(
    console: Any,
    title: str,
    labels: List[str],
    *,
    context_lines: Optional[List[str]] = None,
) -> Optional[int]:
    selected = _arrow_select(title, labels, console, start_index=0, context_lines=context_lines)
    if selected is not None:
        return selected
    if console is None:
        print(f"\n=== {title} ===")
    else:
        console.print(f"\n[bold cyan]{title}[/bold cyan]")
    for idx, label in enumerate(labels, start=1):
        print(f"{idx}. {label}")
    choice = _prompt(f"Select option (1-{len(labels)})")
    try:
        selected = int(choice) - 1
    except Exception:
        return None
    if 0 <= selected < len(labels):
        return selected
    return None


def _run_find_doric_device(console: Any, config: Dict[str, Any], live_state: LiveState) -> Dict[str, Any]:
    state = _ensure_setup_state(live_state)
    if not _laser_setup_required(config):
        _show_pending_setup(console, "Find Doric Device", ["Laser setup is not required for this run."])
        _pause_return_to_menu(console)
        return config

    stim = config["stimulus"]
    if stim.get("uid"):
        state["laser_selected_label"] = f"UID {stim.get('uid')}"
        state["laser_state"] = "selected"
        state["laser_ready"] = False
        state["laser_last_error"] = ""
        active_channels = resolve_active_stimulus_channels(config)
        _show_pending_setup(
            console,
            "Find Doric Device",
            [
                f"Using configured Doric UID: {stim.get('uid')}",
                "Port scanning skipped because UID access is configured.",
                f"Configured control_mode: {stim.get('control_mode')}",
                f"Configured current: {summarize_channel_currents(stim.get('channels', {}))}",
                f"Intended target channels: {summarize_channels(active_channels)}",
                "[yellow]Final verification and arming happen once during launch and should persist into the experiment.[/yellow]",
            ],
        )
        _pause_return_to_menu(console)
        return config

    discovery_cfg = stim.get("discovery", {}) if isinstance(stim.get("discovery", {}), dict) else {}
    usb_vid = str(discovery_cfg.get("usb_vid", "") or "").strip()
    usb_pid = str(discovery_cfg.get("usb_pid", "") or "").strip()
    usb_matches = []
    if usb_vid or usb_pid:
        usb_matches = list_windows_usb_devices(
            target_vid=usb_vid or None,
            target_pid=usb_pid or None,
        )
    preferred_ports, fallback_ports = split_doric_probe_candidates(stim)
    probe_wait_ms = int(discovery_cfg.get("probe_wait_ms", 500) or 500)

    try:
        probe_results = []
        if preferred_ports:
            probe_results = probe_doric_ports(str(stim.get("dll_path", "")), preferred_ports, wait_ms=probe_wait_ms)
        valid_results = [result for result in probe_results if result.ok]
        if not valid_results and fallback_ports:
            _show_pending_setup(
                console,
                "Find Doric Device",
                [
                    f"No usable Doric device found in preferred ports: {', '.join(str(port) for port in preferred_ports) or '[none]'}",
                    f"Fallback scan available across: {', '.join(str(port) for port in fallback_ports) or '[none]'}",
                ],
            )
            approved_scan = _confirm_choice(
                console,
                "Fallback Doric Scan",
                "No usable device was found in the preferred port range. Scan the fallback ports?",
                confirm_label="Scan Fallback Ports",
                cancel_label="Skip Fallback Scan",
            )
            if approved_scan:
                fallback_results = probe_doric_ports(str(stim.get("dll_path", "")), fallback_ports, wait_ms=probe_wait_ms)
                probe_results.extend(fallback_results)
    except Exception as exc:
        state["laser_state"] = "failed"
        state["laser_ready"] = False
        state["laser_last_error"] = str(exc)
        lines = [f"Laser probe failed: {exc}"]
        if usb_matches:
            lines.append("Matching Windows USB devices:")
            lines.extend(summarize_windows_usb_matches(usb_matches))
        _show_pending_setup(console, "Find Doric Device", lines)
        _pause_return_to_menu(console)
        return config

    valid_results = [result for result in probe_results if result.ok]
    invalid_results = [result for result in probe_results if not result.ok]
    if not valid_results:
        lines = _build_doric_failure_lines(
            preferred_ports + fallback_ports,
            invalid_results,
            usb_matches,
            discovery_cfg,
            title="No usable Doric laser ports were identified from the current probe set.",
        )
        _show_pending_setup(console, "Find Doric Device", lines)
        _pause_return_to_menu(console)
        return config

    labels = []
    for result in valid_results:
        labels.append(f"Port {result.port} | open={result.open_result} close={result.close_result}")

    selected = _select_from_labels(console, "Select Doric Device", labels)
    if selected is None:
        _show_pending_setup(console, "Find Doric Device", ["No Doric device selected."])
        _pause_return_to_menu(console)
        return config

    chosen = valid_results[selected]
    config["stimulus"]["port"] = chosen.port
    config["stimulus"]["uid"] = None
    state["laser_selected_label"] = labels[selected]
    state["laser_state"] = "selected"
    state["laser_ready"] = False
    state["laser_last_error"] = ""
    active_channels = resolve_active_stimulus_channels(config)
    lines = [
        f"Selected Doric device: {labels[selected]}",
        f"Configured control_mode: {stim.get('control_mode')}",
        f"Configured current: {summarize_channel_currents(stim.get('channels', {}))}",
        f"Intended target channels: {summarize_channels(active_channels)}",
        "No hardware verification was run yet.",
        "[yellow]Final verification and arming happen once during launch and should persist into the experiment.[/yellow]",
    ]
    if discovery_cfg.get("usb_vid") or discovery_cfg.get("usb_pid"):
        lines.append("Matching Windows USB devices:")
        lines.extend(summarize_windows_usb_matches(usb_matches))
    _show_pending_setup(console, "Find Doric Device", lines)
    _pause_return_to_menu(console)
    return config


def _run_discover_laser_ports(console: Any, config: Dict[str, Any]) -> Dict[str, Any]:
    return config


def _run_test_laser_output(console: Any, config: Dict[str, Any], live_state: LiveState) -> Dict[str, Any]:
    setup = _ensure_setup_state(live_state)
    if not _laser_setup_required(config):
        _show_pending_setup(console, "Test Laser Output w/o Animals", ["Laser setup is not required for this run."])
        _pause_return_to_menu(console)
        return config
    if not (config.get("stimulus", {}).get("uid") or config.get("stimulus", {}).get("port")):
        _show_pending_setup(
            console,
            "Test Laser Output w/o Animals",
            ["Find Doric Device first so the bench test uses a confirmed live Doric device."],
        )
        _pause_return_to_menu(console)
        return config
    if _teensy_setup_required(config) and not setup.get("teensy_ready"):
        _show_pending_setup(
            console,
            "Test Laser Output w/o Animals",
            ["Setup Teensy first. Bench tests are blocked when TTL capture is required for this laser run."],
        )
        _pause_return_to_menu(console)
        return config

    active_channels = resolve_active_stimulus_channels(config)
    if not active_channels:
        fallback_channels = config.get("stimulus", {}).get("channels", {})
        if isinstance(fallback_channels, dict):
            active_channels = {
                str(name): int(idx) for name, idx in fallback_channels.items() if str(name).strip()
            }
    if not active_channels:
        _show_pending_setup(
            console,
            "Test Laser Output w/o Animals",
            [
                "No stimulus channels were resolved for this run.",
                "Check `closed_loop.rules[].outputs.laser_channels`, `stimulus.target_channels`, or `stimulus.channels`.",
            ],
        )
        _pause_return_to_menu(console)
        return config

    warning_lines = [
        "This mode emits real laser output.",
        "Use only when no animal is present and the setup is in bench-test conditions.",
        "Use protective eyewear and point any open end away into non-reflective surfaces.",
        "Do not disconnect hardware while output is active.",
        "If no light is observed, stop the test in software first.",
        "Then change only one hardware connection point at a time before retrying.",
        f"uid={config['stimulus'].get('uid') or '[not configured]'}",
        f"port={config['stimulus'].get('port') or '[not configured]'}",
        f"ttl_output={bool(config['stimulus']['square'].get('ttl_output'))}",
        f"target_channels={summarize_channels(active_channels)}",
    ]
    _show_caution_setup(console, "Test Laser Output w/o Animals", warning_lines)
    approved = _confirm_choice(
        console,
        "Bench Test Confirmation",
        "This bench test emits real laser output. Use it only with no animals present.",
        confirm_label="Proceed With Bench Test",
    )
    if not approved:
        _pause_return_to_menu(console)
        return config

    setup["bench_state"] = "running"
    setup["bench_detail"] = summarize_channels(active_channels)
    try:
        laser = connect_laser_for_run(
            dll_path=str(config["stimulus"]["dll_path"]),
            port=int(config["stimulus"]["port"]) if config["stimulus"].get("port") is not None else None,
            uid=config["stimulus"].get("uid"),
            channel_map=_build_channel_hw_map(active_channels),
            square_cfg=config["stimulus"]["square"],
        )
        try:
            for channel_name, channel_entry in active_channels.items():
                ch_idx = channel_entry.get("index", "?") if isinstance(channel_entry, dict) else channel_entry
                ch_current = channel_entry.get("current_ma", "?") if isinstance(channel_entry, dict) else "?"
                channel_lines = [
                    f"Active test channel: {channel_name}",
                    f"uid={config['stimulus'].get('uid') or '[not configured]'}",
                    f"port={config['stimulus'].get('port') or '[not configured]'}",
                    f"channel_index={ch_idx}",
                    f"current_ma={ch_current}",
                    f"period_ms={config['stimulus']['square'].get('period_ms')}",
                    f"time_on_ms={config['stimulus']['square'].get('time_on_ms')}",
                    f"ttl_output={bool(config['stimulus']['square'].get('ttl_output'))}",
                    "Real output will remain active until you confirm and continue.",
                ]
                _show_pending_setup(console, "Test Laser Output w/o Animals", channel_lines)
                try:
                    laser.start_channel(channel_name)
                    approved_channel = _confirm_choice(
                        console,
                        f"Verify Channel {channel_name}",
                        (
                            f"Confirm visible output for channel {channel_name} and verify the configured "
                            "power/current on hardware. Output remains active until you choose."
                        ),
                        confirm_label="Output Seen - Continue",
                        cancel_label="Stop Test",
                    )
                finally:
                    laser.stop_channel(channel_name)
                if not approved_channel:
                    raise RuntimeError(f"Operator did not confirm active output for channel {channel_name}.")
        finally:
            laser.close()
    except Exception as exc:
        setup["bench_state"] = "failed"
        setup["bench_detail"] = str(exc)
        _show_pending_setup(console, "Test Laser Output w/o Animals", [f"Laser output test failed: {exc}"])
        _pause_return_to_menu(console)
        return config

    setup["bench_state"] = "passed"
    setup["bench_detail"] = summarize_channels(active_channels)
    _show_pending_setup(console, "Test Laser Output w/o Animals", ["Laser output test completed."])
    _pause_return_to_menu(console)
    return config


def _run_setup_teensy(console: Any, config: Dict[str, Any], live_state: LiveState) -> Dict[str, Any]:
    state = _ensure_setup_state(live_state)
    if not _teensy_setup_required(config):
        _show_pending_setup(console, "Setup Teensy", ["Teensy setup is not required for this run."])
        _pause_return_to_menu(console)
        return config

    candidates = list_serial_candidates()
    if not candidates:
        state["teensy_state"] = "failed"
        state["teensy_ready"] = False
        state["teensy_last_error"] = "No serial devices detected."
        _show_pending_setup(console, "Setup Teensy", ["No serial devices detected."])
        _pause_return_to_menu(console)
        return config

    labels = [candidate.label() for candidate in candidates]
    selected = _select_from_labels(console, "Select Teensy", labels)
    if selected is None:
        _show_pending_setup(console, "Setup Teensy", ["No Teensy selected."])
        _pause_return_to_menu(console)
        return config

    chosen = candidates[selected]
    config["ttl_capture"]["port"] = chosen.device
    state["teensy_selected_label"] = labels[selected]
    state["teensy_state"] = "selected"
    state["teensy_ready"] = False
    state["teensy_last_error"] = ""
    try:
        handshake = probe_teensy_handshake(
            port=chosen.device,
            baudrate=int(config["ttl_capture"].get("baudrate", 115200)),
            timeout_seconds=float(config["ttl_capture"].get("serial_timeout_seconds", 0.25)),
            read_chunk_bytes=int(config["ttl_capture"].get("read_chunk_bytes", 4096)),
        )
    except Exception as exc:
        state["teensy_state"] = "failed"
        state["teensy_last_error"] = str(exc)
        _show_pending_setup(console, "Setup Teensy", [f"Teensy handshake failed: {exc}"])
        _pause_return_to_menu(console)
        return config

    state["teensy_state"] = "ready"
    state["teensy_ready"] = True
    _show_pending_setup(
        console,
        "Setup Teensy",
        [
            f"Selected serial device: {labels[selected]}",
            f"Handshake OK: sample_rate={handshake['sampling_rate_hz']}Hz frame_size={handshake['frame_size']}",
            f"Firmware={handshake['firmware_version']} git={handshake['git_hash']}",
        ],
    )
    _pause_return_to_menu(console)
    return config


def _confirm_arm_and_start(config: Dict[str, Any], live_state: LiveState, console: Any = None) -> bool:
    stim = config.get("stimulus", {})
    warnings = get_stim_warnings(stim)
    if warnings:
        _print_warning_block(console, warnings, title="Safety Warnings")
    checklist_lines = _build_launch_checklist(config, live_state)
    if console is None:
        print("=== Verify Experiment Setup ===")
        for line in checklist_lines:
            print(line)
        print("")
    else:
        from ui_renderers import StatusItem, build_launch_summary

        console.print(
            build_launch_summary(
                readiness_items=[
                    StatusItem(label=label, state=state, detail=detail)
                    for label, state, detail in _build_preflight_status_items(config, live_state)
                ],
                summary_sections=_build_session_summary_sections(config, live_state),
                warnings=warnings,
                note="The configured stimulus start schedule begins after you confirm launch. This is the moment to finish setting up your animals if you haven't.",
            )
        )
        console.print("")
    if console is None:
        _print_lines(console, _build_launch_contract_lines(config))

    setup = _ensure_setup_state(live_state)
    if _laser_setup_required(config) and not (
        config.get("stimulus", {}).get("uid") or config.get("stimulus", {}).get("port")
    ):
        if console is None:
            print("Laser launch is blocked: Find Doric Device must complete successfully.")
        else:
            console.print(
                "[bold red]Laser launch is blocked:[/bold red] Find Doric Device must complete successfully."
            )
        return False
    if _teensy_setup_required(config) and not setup.get("teensy_ready"):
        if console is None:
            print("Laser launch is blocked: Setup Teensy must complete successfully.")
        else:
            console.print(
                "[bold red]Laser launch is blocked:[/bold red] Setup Teensy must complete successfully."
            )
        return False

    updated_config = _prompt_closed_loop_assignments(config, console=console)
    if updated_config is not config:
        config.clear()
        config.update(updated_config)
    missing_closed_loop_assignments = _closed_loop_unassigned_rule_ids(config)
    if missing_closed_loop_assignments:
        _show_closed_loop_assignment_block(console, missing_closed_loop_assignments)
        return False
    updated_config = _prompt_open_loop_assignments(config, console=console)
    if updated_config is not config:
        config.clear()
        config.update(updated_config)
    _prompt_session_label(config, live_state, console=console)

    _render_stim_view(console, config, live_state)
    if console is not None:
        console.print("")

    if not _laser_setup_required(config):
        approved = _confirm_choice(
            console,
            "Launch Confirmation",
            "Session time starts when you confirm launch. This is the moment to finish setting up your animals if you haven't.",
            confirm_label="Launch Experiment",
        )
        if approved:
            live_state.launch_confirmed_at = datetime.now()
        return approved
    return True


def _arm_laser_for_launch(config: Dict[str, Any], live_state: LiveState, console: Any = None) -> bool:
    if not _laser_setup_required(config):
        return True

    state = _ensure_setup_state(live_state)
    existing_driver = live_state.laser_driver
    if existing_driver is not None:
        state["laser_ready"] = True
        state["laser_state"] = "armed"
        return True

    active_channels = resolve_active_stimulus_channels(config)
    lines = [
        f"uid={config.get('stimulus', {}).get('uid') or '[not configured]'}",
        f"port={config.get('stimulus', {}).get('port') or '[not configured]'}",
        f"current={summarize_channel_currents(config.get('stimulus', {}).get('channels', {}))}",
        f"target_channels={summarize_channels(active_channels)}",
        "[yellow]The laser will be connected now and kept armed for the experiment.[/yellow]",
        "[yellow]Expected ready state on hardware: current matches config and the selected Doric device stays armed.[/yellow]",
    ]
    _show_pending_setup(console, "Launch Laser Verification", lines)

    try:
        laser = connect_laser_for_run(
            dll_path=str(config["stimulus"]["dll_path"]),
            port=int(config["stimulus"]["port"]) if config["stimulus"].get("port") is not None else None,
            uid=config["stimulus"].get("uid"),
            channel_map=_build_channel_hw_map(active_channels),
            square_cfg=config["stimulus"]["square"],
        )
    except Exception as exc:
        state["laser_state"] = "failed"
        state["laser_ready"] = False
        state["laser_last_error"] = str(exc)
        _show_pending_setup(console, "Launch Laser Verification", [f"Laser verification failed: {exc}"])
        _pause_return_to_menu(console)
        return False

    live_state.laser_driver = laser
    state["laser_state"] = "armed"
    state["laser_ready"] = True
    state["laser_last_error"] = ""
    _show_pending_setup(
        console,
        "Launch Laser Verification",
        [
            "Laser connected and armed for the experiment.",
            "Confirm the hardware current/power and armed state now; launch will start immediately after confirmation.",
        ],
    )
    _render_stim_view(console, config, live_state)
    if console is not None:
        console.print("")
    approved = _confirm_choice(
        console,
        "Launch Laser Verification",
        "Confirm the current/power values are correct and the selected Doric device is armed.",
        confirm_label="Verify, Arm, And Launch Experiment",
        cancel_label="Abort Launch",
    )
    if approved:
        live_state.launch_confirmed_at = datetime.now()
        return True
    try:
        laser.close()
    finally:
        live_state.laser_driver = None
        state["laser_state"] = "selected"
        state["laser_ready"] = False
    return False


def run_preflight(
    config: Dict[str, Any],
    validator: Optional[ValidatorFn] = None,
) -> Tuple[bool, Dict[str, Any], LiveState]:
    """Run interactive preflight menu before acquisition starts.

    Returns:
        (True, updated_config, live_state) if operator confirms start.
        (False, original_config, live_state) if operator aborts.
    """
    original = _clone_without_runtime(config)
    working = _clone_without_runtime(config)
    live_state = LiveState()
    _ensure_setup_state(live_state)
    console = _try_load_rich()
    cursor = 0

    while True:
        styled_menu = _build_preflight_menu_options(working, live_state)
        selected = _arrow_select(
            "Preflight Main Menu",
            styled_menu,
            console,
            start_index=cursor,
            status_config=working,
            live_state=live_state,
        )
        if selected is None:
            print("\n=== Preflight Main Menu ===")
            print("1. Find Doric Device")
            print("2. Setup Teensy")
            print("3. Test Laser Program w/o Animals")
            print("4. Launch Experiment")
            print("5. Show Config")
            print("6. Exit")
            choice = _prompt("Select option (1-6)")
            try:
                selected = int(choice) - 1
            except Exception:
                selected = -1
            if not (0 <= selected < len(styled_menu)) or not _option_is_enabled(styled_menu[selected]):
                selected = -1

        cursor = selected
        if selected == 0:
            working = _run_find_doric_device(console, working, live_state)
        elif selected == 1:
            working = _run_setup_teensy(console, working, live_state)
        elif selected == 2:
            working = _run_test_laser_output(console, working, live_state)
        elif selected == 3:
            working = _run_validation_action(working, validator, console=console)
            if _confirm_arm_and_start(working, live_state, console=console) and _arm_laser_for_launch(
                working, live_state, console=console
            ):
                return True, working, live_state
        elif selected == 4:
            _render_config_view(console, working)
            _pause_return_to_menu(console)
        elif selected == 5:
            _reset_setup_state(live_state)
            return False, original, live_state
        else:
            if console is None:
                print("Invalid selection. Choose 1-6.")
            else:
                console.print("[bold red]Invalid selection.[/bold red] Choose 1-6.")

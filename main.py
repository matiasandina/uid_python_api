#!/usr/bin/env python3
"""
Real-Time UID Mouse Matrix Temperature Monitoring System

This project coordinates IP-connected temperature monitoring from UID Mouse
Matrices, temperature logging, live UI, and stimulation control through the
Doric laser API.

Usage:
    python main.py              # Run with live devices
    python main.py --demo       # Run in demo mode with simulated data

Acknowledgment:
    The TCP module builds on earlier contribution work by Derek Jordan.
    The current runtime, UI, stimulation, replay, TTL capture, and configuration
    system were created and are maintained by Matias Andina.
"""

import sys
import argparse
import re
from pathlib import Path
import signal
import time
from datetime import datetime
from typing import List, Tuple, Any, Dict

from device_manager import DeviceManager
from live_state import LiveState
from project_metadata import CLI_EPILOG, PROJECT_TITLE, get_build_info
from session_metadata import write_session_metadata
from typed_config import load_config, validate_config


def _print_config_error(message: str) -> None:
    try:
        from rich.console import Console
        from rich.console import Group
        from rich.panel import Panel
        from rich.text import Text
    except ImportError:
        print(f"Config error: {message}")
        return

    def style_inline_code(match: re.Match[str]) -> str:
        return f"[bold cyan]{match.group(1)}[/bold cyan]"

    markup = re.sub(r"`([^`]+)`", style_inline_code, message)
    title = "[bold red]Config Error[/bold red]"
    field_match = re.search(r"field \[bold cyan]([^[]+)\[/bold cyan]", markup)
    if field_match:
        title = f"[bold red]Config Error: {field_match.group(1)}[/bold red]"

    parts = [part.strip() for part in re.split(r"(?<=\.)\s+", markup) if part.strip()]
    lines = []
    if parts:
        lines.append(Text.from_markup(parts[0]))
    for part in parts[1:]:
        lines.append(Text.from_markup(f"[bold]Fix:[/bold] {part}"))

    Console().print(
        Panel.fit(
            Group(*lines),
            title=title,
            border_style="red",
        )
    )


def _print_stop_confirmation() -> None:
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text
    except ImportError:
        print("\nStop recording and stimulation? [y/N]")
        print("Press Ctrl+C again at this prompt to confirm safe shutdown.")
        return

    body = Text()
    body.append("Stop recording and stimulation?\n", style="bold white")
    body.append("This will stop outputs, flush data, write metadata, close Doric, and exit.\n", style="white")
    body.append("Choose ", style="white")
    body.append("y", style="bold green")
    body.append(" to stop or ", style="white")
    body.append("n", style="bold yellow")
    body.append(" to resume. Press ", style="white")
    body.append("Ctrl+C", style="bold cyan")
    body.append(" again at this prompt to confirm the same safe shutdown.", style="white")

    Console().print(
        Panel.fit(
            body,
            title="[bold yellow]Confirm Stop[/bold yellow]",
            border_style="yellow",
        )
    )


def _confirm_safe_stop() -> bool:
    _print_stop_confirmation()
    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        response = input("Stop recording and stimulation? [y/N]: ").strip().lower()
    except KeyboardInterrupt:
        print("\nSafe shutdown confirmed.")
        return True
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
    if response in {"y", "yes"}:
        print("Safe shutdown confirmed.")
        return True
    print("Stop canceled. Resuming run.")
    return False


def _describe_stimulus(config: Dict[str, Any]) -> str:
    square = config["square"]
    train = config.get("train", {})
    derived = config.get("derived", {})
    return (
        f"enabled={config['enabled']} mode={config['mode']} control_mode={config.get('control_mode')} "
        f"port={config['port']} window_on={config['window_on_seconds']}s "
        f"period_ms={square['period_ms']} "
        f"time_on_ms={square['time_on_ms']} train_on={train.get('on_seconds')}s "
        f"train_off={train.get('off_seconds')}s pulse_hz={derived.get('pulse_frequency_hz')} "
        f"eff_duty={derived.get('effective_duty')} ttl_output={square['ttl_output']} "
        f"open_loop_channels={config.get('target_channels')} "
        f"open_loop_run_min={config.get('run_for_minutes')}"
    )


def build_devices(devices_cfg: List[Dict[str, Any]]) -> List[Tuple[str, int, str]]:
    devices: List[Tuple[str, int, str]] = []
    for idx, item in enumerate(devices_cfg):
        if not isinstance(item, dict):
            continue
        host = item.get("host")
        port = item.get("port")
        name = item.get("name") or f"Reader-{idx + 1}"
        if not host or not port:
            continue
        devices.append((str(host), int(port), str(name)))
    return devices


def build_device_identity_map(devices_cfg: List[Dict[str, Any]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for idx, item in enumerate(devices_cfg):
        if not isinstance(item, dict):
            continue
        host = str(item.get("host") or "").strip()
        name = str(item.get("name") or f"Reader-{idx + 1}").strip()
        if name:
            mapping[name] = name
        if host:
            mapping[host] = name or host
    return mapping


def get_closed_loop_missing_threshold(config: Dict[str, Any]) -> float | None:
    values: List[float] = []
    for rule in config.get("closed_loop", {}).get("rules", []):
        classifier_cfg = rule.get("classifier", {})
        try:
            value = float(classifier_cfg.get("missing_animal_seconds"))
        except Exception:
            continue
        if value > 0:
            values.append(value)
    return max(values) if values else None


# =============================================================================
# MAIN APPLICATION
# =============================================================================

def _resolve_protocol_name(config_path: str | None, config: Dict[str, Any]) -> str | None:
    if config_path:
        return Path(config_path).stem
    return config.get("session_description")


def main(config: Dict[str, Any], live_state: LiveState | None = None, *, config_path: str | None = None) -> int:
    """
    Main entry point for the temperature monitoring application.
    
    Returns:
        Exit code (0 for success, non-zero for errors)
    """
    # Create the device manager
    start_time = datetime.now()
    manager = DeviceManager(
        output_directory=config["output_directory"],
        averaging_window_seconds=config["data"]["averaging_window_seconds"],
        display_interval_seconds=config["display"]["display_interval_seconds"],
        stale_timeout_seconds=config["network"]["stale_timeout_seconds"],
        reconnect_delay_seconds=config["network"]["reconnect_delay_seconds"],
        health_check_interval_seconds=config["network"]["health_check_interval_seconds"],
        quiet_mode=True,  # Suppress console output for GUI mode
        session_description=live_state.session_label if live_state is not None else config.get("session_description"),
        closed_loop_config=config.get("closed_loop", {"rules": []}),
        device_identity_map=build_device_identity_map(config.get("devices", [])),
        stimulus_config=config["stimulus"],
        ttl_capture_config=config["ttl_capture"],
        live_state=live_state,
    )

    if config["stimulus"]["enabled"]:
        print(f"Stimulus config: {_describe_stimulus(config['stimulus'])}")
    
    print("Setting up UI...")
    from console_display import ConsoleDisplay
    
    # Create the console display with configurable refresh rate
    display = ConsoleDisplay(
        manager=manager,
        output_directory=manager._session_folder,
        refresh_hz=config["display"]["refresh_hz"],
        missing_animal_seconds=get_closed_loop_missing_threshold(config),
    )

    shutdown_requested = False
    stop_confirmation_pending = False
    
    # Set up signal handlers for graceful shutdown (Ctrl+C)
    def signal_handler(signum, frame):
        nonlocal shutdown_requested, stop_confirmation_pending
        if signum == signal.SIGTERM:
            shutdown_requested = True
            manager._is_running = False
            return
        if shutdown_requested:
            return
        stop_confirmation_pending = True
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("Connecting to devices...")
    
    devices = build_devices(config.get("devices", []))
    
    # Add all configured devices
    for host, port, name in devices:
        manager.add_device(
            host=host,
            port=port,
            device_name=name,
            timeout_seconds=config["network"]["socket_timeout_seconds"],
        )
    
    if manager.device_count == 0 and not config["stimulus"]["enabled"]:
        print("Error: No devices configured. Check config.local.yaml and any selected overlay.")
        return 1
    if manager.device_count == 0 and config["stimulus"]["enabled"]:
        print("No UID readers configured; continuing with stimulation-only runtime.")
    
    try:
        # Start all devices with auto-reconnect enabled
        manager.start_all(auto_reconnect=True)
        
        # Start the console display
        print("Launching UI...")
        display.start()
        
        # Keep the main thread alive
        while manager.is_running and not shutdown_requested:
            if stop_confirmation_pending:
                stop_confirmation_pending = False
                display.stop()
                if _confirm_safe_stop():
                    shutdown_requested = True
                    manager._is_running = False
                    continue
                if manager.is_running and not shutdown_requested:
                    display.start()
            time.sleep(0.1)
                
    except KeyboardInterrupt:
        pass
    finally:
        display.stop()
        manager.stop_all()
        end_time = datetime.now()
        write_session_metadata(
            output_directory=config["output_directory"],
            session_folder=manager._session_folder,
            config=config,
            mode="live",
            start_time=start_time,
            end_time=end_time,
            trigger_events=manager.get_trigger_events(),
            missing_events=manager.get_missing_events(),
            source=None,
            session_label=live_state.session_label if live_state is not None else None,
            protocol_name=_resolve_protocol_name(config_path, config),
            build_info=get_build_info(),
        )
    
    return 0


def run_demo_mode(config: Dict[str, Any]) -> int:
    """
    Run in demo mode with simulated data for testing.
    
    Useful for testing the system without actual hardware.
    
    Returns:
        Exit code (0 for success, non-zero for errors)
    """
    import random
    from datetime import datetime
    from animal_registry import AnimalRegistry
    from data_parser import DataParser
    
    print("Running in DEMO MODE (no hardware required)\n")
    
    registry = AnimalRegistry(averaging_window_seconds=config["data"]["averaging_window_seconds"])
    parser = DataParser()
    
    # Sample animal IDs
    animal_ids = ["ABCD1234", "EFGH5678", "IJKL9012", "MNOP3456"]
    
    registry.start_display_loop(interval_seconds=config["display"]["display_interval_seconds"])
    
    try:
        packet_num = 1000
        while True:
            # Generate fake data
            animal_id = random.choice(animal_ids)
            temperature = round(random.uniform(37.5, 39.5), 1)
            zone = random.randint(1, 8)
            
            # Create fake raw data string
            raw_data = f";{packet_num};{zone};{animal_id},{temperature};"
            
            # Parse it (to test the parser)
            readings = parser.parse(raw_data)
            
            for reading in readings:
                registry.record_reading(
                    animal_id=reading.animal_id,
                    temperature=reading.temperature,
                    zone=reading.zone,
                    packet_number=reading.packet_number,
                    timestamp=reading.timestamp
                )
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [DEMO] {reading}")
            
            packet_num += 1
            time.sleep(random.uniform(0.5, 2.0))
            
    except KeyboardInterrupt:
        print("\nStopping demo...")
    finally:
        registry.stop_display_loop()
        
        print("\nDemo Summary:")
        print(f"  Animals tracked: {registry.animal_count}")
        print(f"  Total readings: {registry.total_readings}")
    
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=PROJECT_TITLE,
        epilog=CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--demo", action="store_true", help="Run in demo mode with simulated data")
    parser.add_argument("--config", help="Optional overlay YAML config file")
    args = parser.parse_args()
    
    try:
        config = load_config(args.config, require_local=not args.demo)
    except FileNotFoundError as exc:
        missing = Path(str(exc))
        if missing.name == "config.local.yaml":
            _print_config_error(
                "`config.local.yaml` was not found. "
                "Copy `config.example.yaml` to `config.local.yaml` and fill in the machine-specific values."
            )
        else:
            _print_config_error(f"config file not found: `{missing}`")
        sys.exit(1)
    except ValueError as exc:
        _print_config_error(str(exc))
        sys.exit(1)
    
    if args.demo:
        sys.exit(run_demo_mode(config))

    from preflight_ui import run_preflight

    try:
        approved, config, live_state = run_preflight(config, validator=validate_config)
    except KeyboardInterrupt:
        print("\nPreflight interrupted. Exiting without starting acquisition.")
        sys.exit(0)

    if not approved:
        print("Preflight aborted. Exiting without starting acquisition.")
        sys.exit(0)

    sys.exit(main(config, live_state, config_path=args.config))

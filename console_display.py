"""
Console Display module - Renders a live updating console UI with Rich.

Provides a clean, formatted display that refreshes at a configurable rate
showing device status, animal tracking, and system information.
"""

import math
import os
import sys
import threading
from datetime import datetime
import time
from typing import TYPE_CHECKING, Optional, List, Any, Callable

from project_metadata import get_build_label

if TYPE_CHECKING:
    from device_manager import DeviceManager


class ConsoleDisplay:
    """
    Renders a live updating console display for the monitoring system.
    
    Clears and redraws the entire console every refresh interval to show:
    - Header with system info and disclaimers
    - Device connection status table
    - Animal tracking table with temperatures
    
    Example:
        >>> display = ConsoleDisplay(manager)
        >>> display.start()
        >>> # ... system runs ...
        >>> display.stop()
    """
    
    # Display constants
    DEFAULT_REFRESH_HZ = 60
    DEFAULT_PAGE_SIZE = 12
    TRIGGER_PAGE_SIZE = 1
    MIN_UI_WIDTH = 132
    MIN_UI_HEIGHT = 34
    
    def __init__(
        self,
        manager: 'DeviceManager',
        output_directory: str = "./data",
        refresh_hz: float = DEFAULT_REFRESH_HZ,
        missing_animal_seconds: Optional[float] = None,
    ):
        """
        Initialize the console display.
        
        Args:
            manager: The DeviceManager instance to display data from
            output_directory: The output directory path for display
            refresh_hz: Display refresh rate in Hz (frames per second)
        """
        self._manager = manager
        self._output_directory = output_directory
        self._refresh_interval = 1.0 / max(1.0, refresh_hz)  # Convert Hz to seconds
        self._display_thread: Optional[threading.Thread] = None
        self._key_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_running = False
        self._rich: Optional[Callable[[], None]] = None
        self._Console = None
        self._Group = None
        self._Live = None
        self._Layout = None
        self._Panel = None
        self._Table = None
        self._Text = None
        self._box = None
        self._console = None
        self._page_index = 0
        self._page_size = self.DEFAULT_PAGE_SIZE
        self._page_lock = threading.Lock()
        self._rich_lock = threading.Lock()
        self._rich_loaded = False
        self._missing_animal_seconds = missing_animal_seconds

    def _load_rich(self) -> None:
        """Import Rich lazily to avoid slow startup before setup messages."""
        if self._rich_loaded:
            return
        try:
            from rich.console import Console, Group
            from rich.live import Live
            from rich.layout import Layout
            from rich.align import Align
            from rich.panel import Panel
            from rich.table import Table
            from rich.text import Text
            from rich import box
        except ImportError as exc:
            raise ImportError(
                "rich is required for the console UI. Install with: pip install rich"
            ) from exc
        
        self._Console = Console
        self._Group = Group
        self._Live = Live
        self._Layout = Layout
        self._Align = Align
        self._Panel = Panel
        self._Table = Table
        self._Text = Text
        self._box = box
        self._console = Console()
        self._rich_loaded = True

    def _ensure_rich(self) -> None:
        with self._rich_lock:
            self._load_rich()
    
    # region Properties
    
    @property
    def is_running(self) -> bool:
        """Returns True if the display loop is running."""
        return self._is_running
    
    # endregion
    
    # region Public Methods
    
    def start(self) -> None:
        """Start the display refresh loop."""
        if self._is_running:
            return
        
        self._stop_event.clear()
        self._is_running = True
        
        self._start_key_listener()
        self._display_thread = threading.Thread(
            target=self._display_loop,
            daemon=True,
            name="ConsoleDisplay-Refresh"
        )
        self._display_thread.start()
    
    def stop(self) -> None:
        """Stop the display refresh loop."""
        self._stop_event.set()
        self._is_running = False
        
        if self._key_thread and self._key_thread.is_alive():
            self._key_thread.join(timeout=1.0)
        if self._display_thread and self._display_thread.is_alive():
            self._display_thread.join(timeout=1.0)

    def render_once(self) -> None:
        """Render the display once (useful for final state display)."""
        self._ensure_rich()
        self._wait_for_terminal_size()
        self._console.print(self._build_renderable())
    
    # endregion
    
    # region Private Methods - Display Loop
    
    def _display_loop(self) -> None:
        """Main display loop that refreshes the console."""
        self._ensure_rich()
        self._wait_for_terminal_size()
        with self._Live(
            self._build_renderable(),
            console=self._console,
            refresh_per_second=max(1.0, 1.0 / self._refresh_interval),
            screen=True,
        ) as live:
            while not self._stop_event.is_set():
                try:
                    if self._terminal_size_ok():
                        live.update(self._build_renderable())
                    else:
                        live.update(self._build_resize_renderable())
                except Exception:
                    # Don't let display errors crash the system
                    pass
                
                self._stop_event.wait(self._refresh_interval)

    def _terminal_size_ok(self) -> bool:
        try:
            size = self._console.size
        except Exception:
            return True
        return size.width >= self.MIN_UI_WIDTH and size.height >= self.MIN_UI_HEIGHT

    def _wait_for_terminal_size(self) -> None:
        if self._console is None:
            return
        while not self._stop_event.is_set() and not self._terminal_size_ok():
            self._console.clear()
            self._console.print(self._build_resize_renderable())
            self._stop_event.wait(0.25)

    def _build_resize_renderable(self) -> Any:
        """Build the resize guidance panel shown when the terminal is too small."""
        size = self._console.size
        message = self._Text()
        message.append("Terminal too small for the full monitoring UI.\n\n", style="bold yellow")
        message.append("Please enlarge the terminal window or reduce terminal font size until the UI fits.\n\n", style="white")
        message.append("Recommended minimum size:\n", style="bold white")
        message.append(f"- Width: {self.MIN_UI_WIDTH} columns\n", style="bold cyan")
        message.append(f"- Height: {self.MIN_UI_HEIGHT} rows\n\n", style="bold cyan")
        message.append("Current size:\n", style="bold white")
        message.append(f"- Width: {size.width} columns\n", style="white")
        message.append(f"- Height: {size.height} rows\n\n", style="white")
        message.append("Windows tip:\n", style="bold white")
        message.append("- In native cmd.exe, Ctrl+- reduces zoom and Ctrl++ increases zoom.\n", style="white")
        message.append("- If the UI looks cramped, reduce zoom or maximize the window.\n\n", style="white")
        message.append("The UI will continue automatically once the terminal is large enough.", style="bold green")
        return self._Panel.fit(
            message,
            title="[bold yellow]Resize Terminal[/bold yellow]",
            border_style="yellow",
        )

    def _build_renderable(self) -> Any:
        """Build the full UI as a single Rich renderable group."""
        page_index, page_count = self._get_page_state()
        layout = self._Layout()
        layout.split(
            self._Layout(self._render_header(), name="header", size=6),
            self._Layout(name="body", ratio=1),
            self._Layout(self._render_footer(), name="footer", size=3),
        )
        layout["body"].split_row(
            self._Layout(name="left", ratio=5),
            self._Layout(name="right", ratio=4, minimum_size=46),
        )
        layout["left"].split(
            self._Layout(self._render_devices_table(page_index, page_count), name="devices", size=10),
            self._Layout(self._render_animals_table(page_index, page_count), name="animals", ratio=1),
        )
        layout["right"].split(
            self._Layout(self._render_experiment_status(), name="status", size=14),
            self._Layout(self._render_triggers_table(page_index, page_count), name="triggers", ratio=1),
        )
        return self._Group(layout)

    def _start_key_listener(self) -> None:
        """Start a background thread to listen for pagination keys."""
        if not sys.stdin.isatty():
            return
        
        self._key_thread = threading.Thread(
            target=self._key_loop,
            daemon=True,
            name="ConsoleDisplay-KeyListener"
        )
        self._key_thread.start()

    def _key_loop(self) -> None:
        """Listen for left/right arrow keys to paginate."""
        if os.name == "nt":
            self._key_loop_windows()
        else:
            self._key_loop_posix()

    def _key_loop_windows(self) -> None:
        import msvcrt
        
        while not self._stop_event.is_set():
            if msvcrt.kbhit():
                first = msvcrt.getch()
                if first in (b"\x00", b"\xe0"):
                    key = msvcrt.getch()
                    if key == b"K":  # Left arrow
                        self._page_prev()
                    elif key == b"M":  # Right arrow
                        self._page_next()
            else:
                time.sleep(0.05)

    def _key_loop_posix(self) -> None:
        import select
        import termios
        import tty
        
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        try:
            while not self._stop_event.is_set():
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                if not ready:
                    continue
                
                ch = sys.stdin.read(1)
                if ch != "\x1b":
                    continue
                
                seq = sys.stdin.read(2)
                if seq == "[D":  # Left arrow
                    self._page_prev()
                elif seq == "[C":  # Right arrow
                    self._page_next()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def _page_next(self) -> None:
        with self._page_lock:
            self._page_index += 1

    def _page_prev(self) -> None:
        with self._page_lock:
            self._page_index = max(0, self._page_index - 1)

    def _get_page_state(self) -> tuple[int, int]:
        with self._manager._lock:
            device_count = len(self._manager._devices)
        animal_count = self._manager.registry.animal_count
        trigger_count = len(self._manager.get_trigger_statuses())
        
        page_count = max(
            1,
            math.ceil(device_count / self._page_size),
            math.ceil(animal_count / self._page_size),
            math.ceil(trigger_count / self.TRIGGER_PAGE_SIZE),
        )
        with self._page_lock:
            if self._page_index >= page_count:
                self._page_index = page_count - 1
            return self._page_index, page_count

    def _get_page_bounds(self, total_items: int, page_index: int) -> tuple[int, int]:
        with self._page_lock:
            start = page_index * self._page_size
            end = start + self._page_size
            return start, end

    def _get_trigger_page_bounds(self, total_items: int, page_index: int) -> tuple[int, int]:
        with self._page_lock:
            start = page_index * self.TRIGGER_PAGE_SIZE
            end = start + self.TRIGGER_PAGE_SIZE
            return start, end
    
    # endregion
    
    # region Private Methods - Section Renderers
    
    def _render_header(self) -> Any:
        """Render the top run overview summary."""
        table = self._Table(
            show_header=False,
            expand=True,
            box=self._box.SIMPLE_HEAVY,
            padding=(0, 1),
        )
        table.add_column(style="bold bright_black", width=18)
        table.add_column(style="bold white", ratio=1)
        table.add_column(style="bold bright_black", width=18)
        table.add_column(style="bold white", ratio=1)
        table.add_row("Devices Configured", str(self._manager.device_count), "Animals Tracked", str(self._manager.registry.animal_count))
        table.add_row("Total Readings", str(self._manager.registry.total_readings), "Session Folder", self._output_directory)
        table.add_row("Data Saved To", self._output_directory, "Navigation", "Left/Right arrows paginate")
        return self._Panel(
            table,
            title=self._Text("Run Overview", style="bold green"),
            border_style="cyan",
            padding=(0, 1),
            box=self._box.ROUNDED,
        )

    def _style_state_text(self, value: str) -> Any:
        state = str(value).strip().lower()
        if state in {"ok", "ready", "completed"}:
            return self._Text(str(value), style="bold green")
        if state in {"active", "laser active"}:
            return self._Text(str(value), style="bold yellow")
        if state in {"failed", "fault", "error", "failed closed"}:
            return self._Text(str(value), style="bold red")
        if state in {"waiting for scheduled start", "waiting for start delay", "stopped", "missing", "pending", "not_started", "not started"}:
            return self._Text(str(value), style="bold cyan")
        if state in {"off", "disabled", "inactive"}:
            return self._Text(str(value), style="bold bright_black")
        return self._Text(str(value), style="bold white")

    def _style_blinking_text(self, value: str, *, active_style: str = "bold yellow") -> Any:
        phase = int(time.time()) % 2
        style = active_style if phase == 0 else "bold white"
        return self._Text(str(value), style=style)

    def _format_runtime_state(self) -> str:
        control_mode = str(self._manager._stimulus_config.get("control_mode", "closed_loop")).lower()
        stim_status = self._manager.get_stimulus_status()
        stim_state = str(stim_status.get("state", "not_started")).lower()
        if control_mode == "open_loop":
            open_loop = self._manager.get_open_loop_status()
            state = str(open_loop.get("state", "not_started")).lower()
            if state == "scheduled":
                return "Waiting for Scheduled Start"
            if state == "running":
                return "Laser Active"
            if state == "completed":
                return "Completed"
            if state == "stopped":
                return "Stopped"
            if state == "error":
                return "Failed Closed"
            return "Not Started"
        if stim_state == "active":
            return "Laser Active"
        if stim_state == "fault":
            return "Failed Closed"
        if stim_state == "ready":
            return "Ready"
        if stim_state == "disabled":
            return "Monitor Only"
        return "Waiting"

    def _format_laser_output(self) -> str:
        stim_status = self._manager.get_stimulus_status()
        mode = str(stim_status.get("mode", self._manager._stimulus_config.get("mode", "monitor"))).lower()
        control_mode = str(self._manager._stimulus_config.get("control_mode", "closed_loop")).lower()
        if mode != "laser":
            return "OFF"
        if control_mode == "open_loop":
            open_loop = self._manager.get_open_loop_status()
            state = str(open_loop.get("state", "not_started")).lower()
            channels = ",".join(open_loop.get("target_channels", [])) or "-"
            if state == "running":
                return f"ACTIVE ON {channels}"
            return "OFF"
        if str(stim_status.get("state", "")).lower() == "active":
            return "ACTIVE"
        return "OFF"

    def _format_total_session_time(self) -> str:
        control_mode = str(self._manager._stimulus_config.get("control_mode", "closed_loop")).lower()
        if control_mode != "open_loop":
            return "Current session / manual exit"
        status = self._manager.get_open_loop_status()
        total_minutes = float(status.get("run_for_minutes", 0.0) or 0.0)
        total_delay = float(status.get("start_delay_seconds", 0.0) or 0.0)
        total_seconds = max(0, int(total_minutes * 60 + total_delay))
        launched_at = status.get("launch_confirmed_at")
        if launched_at is None:
            return f"00:00 / {self._format_seconds(total_seconds)}"
        elapsed = max(0, int((datetime.now() - launched_at).total_seconds()))
        return f"{self._format_seconds(min(elapsed, total_seconds))} / {self._format_seconds(total_seconds)}"

    def _format_stimulation_time(self) -> str:
        control_mode = str(self._manager._stimulus_config.get("control_mode", "closed_loop")).lower()
        if control_mode != "open_loop":
            return "Current session / manual exit"
        status = self._manager.get_open_loop_status()
        total_minutes = float(status.get("run_for_minutes", 0.0) or 0.0)
        total_seconds = max(0, int(total_minutes * 60))
        started_at = status.get("run_started_at")
        if started_at is None:
            return f"00:00 / {self._format_seconds(total_seconds)}"
        elapsed = max(0, int((datetime.now() - started_at).total_seconds()))
        return f"{self._format_seconds(min(elapsed, total_seconds))} / {self._format_seconds(total_seconds)}"

    def _format_time_until_start(self) -> str:
        control_mode = str(self._manager._stimulus_config.get("control_mode", "closed_loop")).lower()
        if control_mode != "open_loop":
            return "Starts on trigger / manual exit"
        status = self._manager.get_open_loop_status()
        state = str(status.get("state", "")).lower()
        scheduled_start_at = status.get("scheduled_start_at")
        if state in {"running", "completed"}:
            return "00:00"
        if scheduled_start_at is None:
            return "00:00"
        remaining = max(0, int((scheduled_start_at - datetime.now(scheduled_start_at.tzinfo)).total_seconds()))
        return self._format_seconds(remaining)

    def _format_seconds(self, total_seconds: int) -> str:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _format_pulse_timing(square_cfg: Any) -> str:
        if not isinstance(square_cfg, dict):
            return "n/a"
        period_ms = square_cfg.get("period_ms")
        time_on_ms = square_cfg.get("time_on_ms")
        if period_ms is None or time_on_ms is None:
            return "n/a"
        try:
            period = float(period_ms)
            time_on = float(time_on_ms)
        except (TypeError, ValueError):
            return "n/a"
        return f"{time_on:g} ms ON / {max(0.0, period - time_on):g} ms OFF"

    def _render_experiment_status(self) -> Any:
        control_mode = str(self._manager._stimulus_config.get("control_mode", "closed_loop")).lower()
        stim_cfg = self._manager._stimulus_config
        closed_loop_cfg = self._manager._closed_loop_config
        ttl_status = self._manager.get_ttl_capture_status()
        square_cfg = stim_cfg.get("square", {})
        train_cfg = stim_cfg.get("train", {})
        pulse_hz = stim_cfg.get("derived", {}).get("pulse_frequency_hz")
        train_on = train_cfg.get("on_seconds", stim_cfg.get("window_on_seconds"))
        train_off = train_cfg.get("off_seconds", 0.0)
        if control_mode == "open_loop":
            target_channels = ",".join(str(ch) for ch in stim_cfg.get("target_channels", []) if str(ch).strip()) or "-"
        else:
            channels: List[str] = []
            for rule in closed_loop_cfg.get("rules", []):
                channels.extend(rule.get("outputs", {}).get("laser_channels", []))
            target_channels = ",".join(str(ch) for ch in channels if str(ch).strip()) or "-"

        table = self._Table(
            show_header=False,
            expand=True,
            box=self._box.SIMPLE_HEAVY,
            padding=(0, 1),
        )
        table.add_column(style="bold bright_black", width=18)
        table.add_column(ratio=1)
        runtime_state = self._format_runtime_state()
        laser_output = self._format_laser_output()
        if runtime_state == "Laser Active":
            runtime_text = self._style_blinking_text(runtime_state)
        else:
            runtime_text = self._style_state_text(runtime_state)
        if laser_output.startswith("ACTIVE"):
            laser_text = self._style_blinking_text(laser_output)
        else:
            laser_text = self._style_state_text(laser_output)
        table.add_row("Current State", runtime_text)
        table.add_row("Laser Output", laser_text)
        if control_mode == "open_loop":
            table.add_row("Total Session Time", self._Text(self._format_total_session_time(), style="bold white"))
            table.add_row("Time Until Start", self._Text(self._format_time_until_start(), style="bold white"))
            table.add_row("Stimulation Time", self._Text(self._format_stimulation_time(), style="bold white"))
        else:
            table.add_row("Total Session Time", self._Text("Current session / manual exit", style="bold white"))
            table.add_row("Time Until Start", self._Text("Starts on trigger / manual exit", style="bold white"))
        table.add_row("Run Mode", self._Text("Open Loop" if control_mode == "open_loop" else "Closed Loop", style="bold cyan"))
        table.add_row("Target Channels", self._Text(target_channels, style="bold white"))
        table.add_row("Pulse Frequency", self._Text(f"{pulse_hz} Hz" if pulse_hz is not None else "n/a", style="bold white"))
        table.add_row(
            "Pulse Timing",
            self._Text(self._format_pulse_timing(square_cfg), style="bold white"),
        )
        train_timing_text = f"{train_on} s ON / {train_off} s OFF"
        train_timing_style = "bold white"
        if float(train_off or 0) == 0:
            train_timing_text += "  !! 100% duty cycle"
            train_timing_style = "bold red"
        table.add_row("Train Timing", self._Text(train_timing_text, style=train_timing_style))
        channels_cfg = stim_cfg.get("channels", {})
        if isinstance(channels_cfg, dict) and channels_cfg:
            current_parts = [f"{name}: {ch.get('current_ma', '?')} mA" for name, ch in channels_cfg.items() if isinstance(ch, dict)]
            current_text = ", ".join(current_parts) if current_parts else "-"
        else:
            current_text = "-"
        table.add_row("Laser Current", self._Text(current_text, style="bold white"))
        ttl_label = "Running" if ttl_status.get("running") else ("Enabled" if ttl_status.get("enabled") else "Disabled")
        table.add_row("TTL Capture", self._style_state_text(ttl_label))
        table.add_row("Data Saved To", self._Text(self._output_directory, style="bold green"))
        return self._Panel(
            table,
            title=self._Text("Experiment Status", style="bold green"),
            border_style="green",
            box=self._box.ROUNDED,
        )
    
    def _render_devices_table(self, page_index: int, page_count: int) -> Any:
        """Render the device status table."""
        devices_list = []
        with self._manager._lock:
            devices_list = list(self._manager._devices.items())
            stats = dict(self._manager._device_stats)
        
        start, end = self._get_page_bounds(len(devices_list), page_index)
        devices_page = devices_list[start:end]
        
        table = self._Table(
            title=f"Devices (Page {page_index + 1}/{page_count})",
            title_style="bold white",
            show_lines=False,
            expand=True,
            header_style="bold magenta",
            box=self._box.MINIMAL,
            row_styles=["none", "dim"],
        )
        table.add_column("Device", style="bold")
        table.add_column("Host:Port", style="white")
        table.add_column("State", justify="center")
        table.add_column("Scans", justify="right")
        table.add_column("Last Zone", justify="right")
        table.add_column("Last Scan")
        table.add_column("Last Change")
        table.add_column("Next Reconnect")
        
        for device_name, device in devices_page:
            stat = stats.get(device_name)
            if not stat:
                continue
            
            host_port = f"{device.config.host}:{device.config.port}"
            
            # Format status
            state = device.state
            if state.name == "CONNECTED":
                status_str = "OK"
                status_style = "green"
            elif state.name == "DISCONNECTED":
                status_str = "DISC"
                status_style = "yellow"
            elif state.name == "RECONNECTING":
                status_str = "RECONN"
                status_style = "cyan"
            elif state.name == "ERROR":
                status_str = "ERROR"
                status_style = "red"
            else:
                status_str = state.name[:6]
                status_style = "white"
            
            # Format total scans
            scans_str = str(stat.readings_received)
            
            # Format last scan time
            if stat.last_reading_time:
                last_scan = stat.last_reading_time.strftime("%Y-%m-%d %I:%M:%S%p").lower()
            else:
                last_scan = ""
            
            # Format last status change
            if stat.last_state_change_time:
                last_change = stat.last_state_change_time.strftime("%Y-%m-%d %I:%M:%S%p").lower()
            else:
                last_change = ""
            
            # Format next reconnect time
            next_reconnect = ""
            if state.name in ("DISCONNECTED", "ERROR", "RECONNECTING"):
                if stat.next_reconnect_time:
                    next_reconnect = stat.next_reconnect_time.strftime("%Y-%m-%d %I:%M:%S%p").lower()
                else:
                    next_reconnect = "Pending..."
            
            # Format last zone
            last_zone = str(stat.last_zone) if stat.last_zone is not None else ""
            
            table.add_row(
                device_name,
                host_port,
                self._Text(status_str, style=status_style),
                scans_str,
                last_zone,
                last_scan,
                last_change,
                next_reconnect,
            )
        
        if not devices_list:
            table.add_row("No devices configured", "", "", "", "", "", "", "")
        
        return self._Panel(table, border_style="magenta", box=self._box.ROUNDED)
    
    def _render_animals_table(self, page_index: int, page_count: int) -> Any:
        """Render the animal tracking table."""
        animals = self._manager.registry.get_all_animals()
        animals.sort(key=lambda a: a.animal_id)
        
        start, end = self._get_page_bounds(len(animals), page_index)
        animals_page = animals[start:end]
        
        table = self._Table(
            title=f"Animals (Page {page_index + 1}/{page_count})",
            title_style="bold white",
            show_lines=False,
            expand=True,
            header_style="bold magenta",
            box=self._box.MINIMAL,
            row_styles=["none", "dim"],
        )
        table.add_column("RFID", style="bold")
        table.add_column("Current Temp (C)", justify="right")
        table.add_column("Last Scanned At")
        table.add_column("Last Zone", justify="right")
        table.add_column("Scans", justify="right")
        table.add_column("Status")
        
        for animal in animals_page:
            rfid = animal.animal_id
            
            # Format temperature - show last temperature, not average
            if animal.last_temperature is not None:
                temp_str = self._Text(f"{animal.last_temperature:.1f}", style="bold white")
            else:
                temp_str = self._Text("N/A", style="dim")
            
            # Format last scanned time
            if animal.last_scan_time:
                last_scanned = animal.last_scan_time.strftime("%Y-%m-%d %I:%M:%S%p").lower()
            else:
                last_scanned = ""
            
            # Format last zone
            if animal.last_zone is not None:
                zone_str = str(animal.last_zone)
            else:
                zone_str = ""
            
            # Format total scans for this animal
            scans_str = str(animal.total_readings)
            status_text = self._Text("OK", style="green")
            if self._missing_animal_seconds is not None:
                seconds_since = animal.seconds_since_last_scan
                if seconds_since is not None and seconds_since > self._missing_animal_seconds:
                    status_text = self._Text("MISSING", style="bold yellow")
            
            table.add_row(rfid, temp_str, last_scanned, zone_str, scans_str, status_text)
        
        if not animals:
            table.add_row("No animals detected yet", "", "", "", "", "")
        
        return self._Panel(table, border_style="magenta", box=self._box.ROUNDED)

    def _render_triggers_table(self, page_index: int, page_count: int) -> Any:
        """Render trigger evaluation and stimulation status by animal."""
        control_mode = str(self._manager._stimulus_config.get("control_mode", "closed_loop")).lower()
        if control_mode == "open_loop":
            return self._render_open_loop_table()

        statuses = self._manager.get_trigger_statuses()
        status_keys = sorted(statuses.keys())
        start, end = self._get_trigger_page_bounds(len(status_keys), page_index)
        status_keys_page = status_keys[start:end]
        rule_count = len(self._manager._closed_loop_config.get("rules", []))
        stim_status = self._manager.get_stimulus_status()
        stim_state = str(stim_status.get("state", "disabled")).upper()
        stim_enabled = bool(stim_status.get("enabled", False))
        stim_mode = str(stim_status.get("mode", "monitor"))
        stim_tag = f"{stim_state} (enabled={stim_enabled} mode={stim_mode})"

        table = self._Table(
            title=f"Closed-Loop Status ({rule_count} rule(s), stim={stim_tag})",
            title_style="bold white",
            show_lines=True,
            expand=True,
            header_style="bold magenta",
            box=self._box.MINIMAL_HEAVY_HEAD,
        )
        table.add_column("Field", style="bold cyan", width=16, no_wrap=True)
        table.add_column("Value", style="white", ratio=1)

        for status_key in status_keys_page:
            status = statuses[status_key]
            decision_text = self._Text("TRIGGER" if status.condition_true else "WAITING")
            decision_text.stylize("bold green" if status.condition_true else "bold yellow")
            avg_temp = f"{status.current_avg_temp:.2f}" if status.current_avg_temp is not None else "-"
            last_event = status.last_event_time.strftime("%Y-%m-%d %I:%M:%S%p").lower() if status.last_event_time else "-"
            window_text = self._format_closed_loop_window(status)
            sample_text = self._format_closed_loop_samples(status)
            threshold_text = self._format_closed_loop_threshold(status, avg_temp)
            table.add_row("Rule", self._Text(status.rule_id or "-", style="bold white"))
            table.add_row("Probe RFID", self._Text(status.animal_id, style="bold white"))
            table.add_row("Device(s)", ",".join(status.device_names) or "-")
            table.add_row("Outputs", self._Text(",".join(status.target_channels) or "-", style="bold white"))
            table.add_row("Decision", decision_text)
            table.add_row("Window", window_text)
            table.add_row("Samples", sample_text)
            table.add_row("Threshold", threshold_text)
            table.add_row("Last Action", self._Text(status.last_action, style="bold white"))
            table.add_row("Triggers", str(status.trigger_count))
            table.add_row("Last Event", last_event)
            table.add_row("Status", status.reason or "-")

        if not statuses:
            table.add_row("Status", "No trigger evaluations yet")
            table.add_row("Meaning", "Waiting for matching RFID readings before classifier output can be shown.")
            table.add_row("Stim Ready", "READY means hardware is armed; output starts only after a trigger event.")

        return self._Panel(table, border_style="magenta", box=self._box.ROUNDED)

    def _format_closed_loop_window(self, status: Any) -> str:
        observed = getattr(status, "observed_duration_seconds", None)
        required = getattr(status, "required_duration_seconds", None)
        tolerance = getattr(status, "coverage_tolerance_seconds", None)
        ready = getattr(status, "coverage_ready", None)
        if observed is None or required is None:
            return "-"
        label = f"{observed:.1f} / {required:.1f} s"
        if tolerance:
            label += f" (+{tolerance:.1f}s tol)"
        if ready is not None:
            label += " READY" if ready else " collecting"
        return label

    def _format_closed_loop_samples(self, status: Any) -> str:
        sample_count = getattr(status, "sample_count", 0)
        min_samples = getattr(status, "min_samples", None)
        if min_samples is None:
            return str(sample_count)
        return f"{sample_count} / {min_samples}"

    def _format_closed_loop_threshold(self, status: Any, avg_temp: str) -> str:
        threshold = getattr(status, "threshold_c", None)
        direction = str(getattr(status, "direction", "") or "below")
        aggregation = str(getattr(status, "aggregation", "") or "mean")
        threshold_met = getattr(status, "threshold_met", None)
        if threshold is None or avg_temp == "-":
            return "-"
        comparator = ">" if direction == "above" else "<"
        label = f"{aggregation} {avg_temp} {comparator} {threshold:g}"
        if threshold_met is not None:
            label += " TRUE" if threshold_met else " FALSE"
        return label

    def _render_open_loop_table(self) -> Any:
        """Render open-loop stimulation status panel."""
        status = self._manager.get_open_loop_status()
        channels = ",".join(status.get("target_channels", [])) or "-"
        started_at = status.get("run_started_at")
        ends_at = status.get("run_ends_at")
        last_event = status.get("last_event_time")

        table = self._Table(
            title="Open-Loop Activity",
            title_style="bold white",
            show_lines=False,
            expand=True,
            header_style="bold magenta",
            box=self._box.MINIMAL,
            row_styles=["none", "dim"],
        )
        table.add_column("Field", style="bold")
        table.add_column("Value")
        program_state = str(status.get("state", "-"))
        if program_state.lower() == "running":
            program_state_text = self._style_blinking_text(program_state)
        else:
            program_state_text = self._style_state_text(program_state)
        table.add_row("Program State", program_state_text)
        table.add_row("Target Channels", channels)
        table.add_row("Planned Stim Duration", self._format_seconds(max(0, int(float(status.get("run_for_minutes", 0.0) or 0.0) * 60))))
        table.add_row("Total Session Time", self._format_total_session_time())
        table.add_row("Start Mode", str(status.get("start_mode", "-")))
        scheduled_start_at = status.get("scheduled_start_at")
        table.add_row(
            "Scheduled Start",
            scheduled_start_at.strftime("%Y-%m-%d %I:%M:%S%p").lower() if scheduled_start_at else "-",
        )
        table.add_row("Time Until Start", self._format_time_until_start())
        table.add_row("Delay Until Start (s)", str(status.get("start_delay_seconds", "-")))
        table.add_row("Stimulation Time", self._format_stimulation_time())
        table.add_row("Pulse Trains Started", str(status.get("pulses_sent", 0)))
        table.add_row(
            "Started At",
            started_at.strftime("%Y-%m-%d %I:%M:%S%p").lower() if started_at else "-",
        )
        table.add_row(
            "Ends At",
            ends_at.strftime("%Y-%m-%d %I:%M:%S%p").lower() if ends_at else "-",
        )
        table.add_row(
            "Last Event",
            last_event.strftime("%Y-%m-%d %I:%M:%S%p").lower() if last_event else "-",
        )
        table.add_row("Last Error", str(status.get("last_error") or "-"))

        return self._Panel(table, border_style="magenta", box=self._box.ROUNDED)
    
    def _render_footer(self) -> Any:
        """Render the footer with timestamp."""
        now = datetime.now()
        text = self._Text()
        text.append(get_build_label(), style="bold cyan")
        text.append("  |  ", style="dim")
        text.append("Last Updated  ", style="dim")
        text.append(now.strftime("%m/%d/%Y %I:%M:%S%p").lower(), style="bold")
        text.append("  |  Press Ctrl+C to stop", style="dim")
        aligned = self._Align.right(text)
        return self._Panel(aligned, border_style="cyan", padding=(0, 2), box=self._box.ROUNDED)
    
    # endregion
    
    # region Context Manager Support
    
    def __enter__(self) -> 'ConsoleDisplay':
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
    
    # endregion

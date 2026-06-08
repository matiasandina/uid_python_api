"""
Device Manager module - Orchestrates multiple IP devices simultaneously.

Manages connections to up to 24 devices, routes data to the appropriate
CSV loggers, and updates the animal registry with parsed readings.
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any, TYPE_CHECKING
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from ip_device import IPDevice, DeviceConfig, DeviceState
from datalogger_csv import MultiDeviceLogger
from data_parser import DataParser, ParsedReading
from animal_registry import AnimalRegistry
from trigger_scheduler import TriggerScheduler, load_classifier, TriggerEvent, TriggerAnimalStatus
from stim_controller import StimulationController
from hardware_setup import resolve_active_stimulus_channels
from live_state import LiveState
from session_naming import build_session_folder_name

if TYPE_CHECKING:
    from ttl_capture.capture import TTLCaptureService


@dataclass
class DeviceStats:
    """Statistics for a single device."""
    device_name: str
    state: DeviceState
    readings_received: int
    last_reading_time: Optional[datetime]
    errors: int
    last_state_change_time: Optional[datetime] = None
    next_reconnect_time: Optional[datetime] = None
    last_zone: Optional[int] = None


@dataclass
class OpenLoopStatus:
    """Live status for open-loop stimulation runs."""
    enabled: bool
    state: str
    control_mode: str
    target_channels: List[str]
    assignments: List[Dict[str, Any]]
    run_for_minutes: float
    start_mode: str
    start_delay_seconds: float
    scheduled_start_at: Optional[datetime] = None
    launch_confirmed_at: Optional[datetime] = None
    run_started_at: Optional[datetime] = None
    run_ends_at: Optional[datetime] = None
    pulses_sent: int = 0
    last_event_time: Optional[datetime] = None
    last_error: Optional[str] = None


class DeviceManager:
    """
    Manages multiple IP devices for 24/7 temperature monitoring.
    
    Coordinates device connections, data parsing, CSV logging, and
    animal registry updates across all connected devices.
    
    Example:
        >>> manager = DeviceManager(output_directory="./data")
        >>> manager.add_device("192.168.1.100", 10001, "Reader-1")
        >>> manager.add_device("192.168.1.101", 10001, "Reader-2")
        >>> manager.start_all()
        >>> # ... run for a while ...
        >>> manager.stop_all()
    """
    
    MAX_DEVICES = 24
    
    # Default intervals for health monitoring
    DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS = 10.0
    DEFAULT_STALE_TIMEOUT_SECONDS = 30.0
    DEFAULT_RECONNECT_DELAY_SECONDS = 30.0
    
    def __init__(self, output_directory: str = "./data", 
                 averaging_window_seconds: int = 60,
                 display_interval_seconds: float = 10.0,
                 stale_timeout_seconds: float = DEFAULT_STALE_TIMEOUT_SECONDS,
                 reconnect_delay_seconds: float = DEFAULT_RECONNECT_DELAY_SECONDS,
                 health_check_interval_seconds: float = DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS,
                 quiet_mode: bool = True,
                 session_description: str | None = None,
                 closed_loop_config: Optional[Dict[str, Any]] = None,
                 device_identity_map: Optional[Dict[str, str]] = None,
                 stimulus_config: Optional[Dict[str, Any]] = None,
                 ttl_capture_config: Optional[Dict[str, Any]] = None,
                 live_state: Optional[LiveState] = None):
        """
        Initialize a new DeviceManager instance.
        
        Args:
            output_directory: Directory for CSV output files
            averaging_window_seconds: Window size for temperature averaging
            display_interval_seconds: How often to display animal status
            stale_timeout_seconds: Reconnect if no data received for this many seconds
            reconnect_delay_seconds: Delay between reconnection attempts
            health_check_interval_seconds: How often to check device health
            quiet_mode: If True, suppress console output (for GUI display mode)
            session_description: Optional user description for the session folder name
        """
        self._devices: Dict[str, IPDevice] = {}
        self._device_stats: Dict[str, DeviceStats] = {}
        self._lock = threading.Lock()
        self._quiet_mode = quiet_mode
        
        # Create timestamped session folder: data/2026_02_04_11_49_23_optional_label/
        session_folder_name = build_session_folder_name(datetime.now(), session_description)
        self._session_folder = f"{output_directory}/{session_folder_name}"
        
        # Shared components
        self._csv_logger = MultiDeviceLogger(output_directory=self._session_folder)
        self._parser = DataParser()
        self._registry = AnimalRegistry(averaging_window_seconds=averaging_window_seconds)
        
        self._display_interval = display_interval_seconds
        self._stale_timeout_seconds = stale_timeout_seconds
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._health_check_interval_seconds = health_check_interval_seconds
        self._is_running = False
        self._closed_loop_config = closed_loop_config or {"rules": []}
        self._device_identity_map = {str(k): str(v) for k, v in (device_identity_map or {}).items()}
        self._stimulus_config = stimulus_config or {"enabled": False}
        self._control_mode = str(self._stimulus_config.get("control_mode", "closed_loop")).strip().lower()
        self._trigger_schedulers: List[TriggerScheduler] = []
        self._trigger_events: List[TriggerEvent] = []
        self._next_animal_event_id: Dict[str, int] = {}
        self._missing_events: List[Dict[str, Any]] = []
        self._stim_controller: Optional[StimulationController] = None
        self._stim_fault_reported = False
        self._live_state = live_state or LiveState()
        self._open_loop_cfg = self._stimulus_config
        open_loop_assignments = self._get_open_loop_assignments()
        self._open_loop_thread: Optional[threading.Thread] = None
        self._stop_open_loop_event = threading.Event()
        self._open_loop_lock = threading.Lock()
        self._open_loop_status = OpenLoopStatus(
            enabled=bool(self._stimulus_config.get("enabled", False) and self._control_mode == "open_loop"),
            state="idle",
            control_mode=self._control_mode,
            target_channels=self._get_open_loop_target_channels(),
            assignments=open_loop_assignments,
            run_for_minutes=float(self._open_loop_cfg.get("run_for_minutes", 0.0) or 0.0),
            start_mode=(
                "delay"
                if self._open_loop_cfg.get("start_delay_seconds") is not None
                else str((self._open_loop_cfg.get("start") or {}).get("mode", "immediate"))
            ),
            start_delay_seconds=float(self._open_loop_cfg.get("start_delay_seconds", 0.0) or 0.0),
        )
        self._ttl_capture_config = ttl_capture_config or {}
        self._ttl_capture: Optional["TTLCaptureService"] = None
        
        # Health monitor thread
        self._health_monitor_thread: Optional[threading.Thread] = None
        self._stop_health_monitor = threading.Event()
        
        # Callbacks
        self.on_reading_received: Optional[Callable[[str, ParsedReading], None]] = None
        self.on_device_error: Optional[Callable[[str, Exception], None]] = None
    
    # region Properties
    
    @property
    def device_count(self) -> int:
        """Gets the number of configured devices."""
        return len(self._devices)
    
    @property
    def is_running(self) -> bool:
        """Returns True if the manager is actively running."""
        return self._is_running
    
    @property
    def registry(self) -> AnimalRegistry:
        """Gets the animal registry."""
        return self._registry
    
    @property
    def parser(self) -> DataParser:
        """Gets the data parser."""
        return self._parser
    
    @property
    def quiet_mode(self) -> bool:
        """Returns True if quiet mode is enabled."""
        return self._quiet_mode
    
    # endregion
    
    # region Private Helpers
    
    def _log(self, message: str) -> None:
        """Print a message if not in quiet mode."""
        if not self._quiet_mode:
            print(message)
    
    # endregion
    
    # region Public Methods - Device Management
    
    def add_device(self, host: str, port: int, device_name: str,
                   timeout_seconds: float = 5.0) -> bool:
        """
        Add a new device to be managed.
        
        Args:
            host: IP address or hostname of the device
            port: TCP port number
            device_name: Human-readable name for the device
            timeout_seconds: Socket timeout for receive operations
        
        Returns:
            True if the device was added successfully, False otherwise
        """
        with self._lock:
            if len(self._devices) >= self.MAX_DEVICES:
                self._log(f"Cannot add device: maximum of {self.MAX_DEVICES} devices reached")
                return False
            
            if device_name in self._devices:
                self._log(f"Device '{device_name}' already exists")
                return False
            
            config = DeviceConfig(
                host=host,
                port=port,
                device_name=device_name,
                timeout_seconds=timeout_seconds,
                reconnect_delay_seconds=self._reconnect_delay_seconds,
                max_reconnect_attempts=0,  # Infinite for 24/7 operation
                stale_timeout_seconds=self._stale_timeout_seconds
            )
            
            device = IPDevice(config, quiet_mode=self._quiet_mode)
            
            # Wire up the data handler
            device.on_data_received = lambda data, dn=device_name: self._handle_device_data(dn, data)
            device.on_error = lambda err, dn=device_name: self._handle_device_error(dn, err)
            
            self._devices[device_name] = device
            self._device_stats[device_name] = DeviceStats(
                device_name=device_name,
                state=DeviceState.DISCONNECTED,
                readings_received=0,
                last_reading_time=None,
                errors=0,
                last_state_change_time=datetime.now(),
                next_reconnect_time=None
            )
            
            self._log(f"Added device: {device_name} ({host}:{port})")
            return True
    
    def remove_device(self, device_name: str) -> bool:
        """
        Remove a device from management.
        
        Args:
            device_name: Name of the device to remove
        
        Returns:
            True if the device was removed, False if not found
        """
        with self._lock:
            if device_name not in self._devices:
                return False
            
            device = self._devices[device_name]
            device.disconnect()
            
            del self._devices[device_name]
            del self._device_stats[device_name]
            
            self._log(f"Removed device: {device_name}")
            return True
    
    def get_device(self, device_name: str) -> Optional[IPDevice]:
        """
        Get a device by name.
        
        Args:
            device_name: Name of the device
        
        Returns:
            IPDevice instance or None if not found
        """
        return self._devices.get(device_name)
    
    def get_device_names(self) -> List[str]:
        """Get list of all device names."""
        return list(self._devices.keys())
    
    # endregion
    
    # region Public Methods - Operations
    
    def start_all(self, auto_reconnect: bool = True) -> None:
        """
        Start all devices and begin data collection.
        
        Args:
            auto_reconnect: Whether to automatically reconnect on failure
        """
        if self._is_running:
            self._log("Device manager is already running")
            return
        
        self._log(f"\nStarting {len(self._devices)} device(s)...")
        
        self._is_running = True
        
        # Start the health monitor thread
        self._start_health_monitor()

        # Start TTL capture if configured
        self._start_ttl_capture()
        
        # Connect and start each device
        for device_name, device in self._devices.items():
            try:
                if device.connect():
                    device.send_command("RRLOOP")
                    device.start_receiving(auto_reconnect=auto_reconnect)
                    self._update_device_state(device_name, DeviceState.CONNECTED)
                else:
                    self._log(f"Failed to connect to {device_name}")
                    self._update_device_state(device_name, DeviceState.ERROR)
            except Exception as e:
                self._log(f"Error starting {device_name}: {e}")
                self._update_device_state(device_name, DeviceState.ERROR)
        
        self._log("All devices started. Press Ctrl+C to stop.\n")
        
        # Start stimulation and selected control plane
        self._start_control_plane()
    
    def stop_all(self) -> None:
        """Stop all devices and close resources."""
        self._log("\nStopping all devices...")
        
        self._is_running = False
        
        # Stop the health monitor
        self._stop_health_monitor_thread()

        # Stop TTL capture
        self._stop_ttl_capture()
        
        # Stop selected control plane and stimulation
        self._stop_control_plane()
        
        # Disconnect all devices
        for device_name, device in self._devices.items():
            try:
                device.disconnect()
                self._update_device_state(device_name, DeviceState.DISCONNECTED)
            except Exception as e:
                self._log(f"Error stopping {device_name}: {e}")
        
        # Close all CSV loggers
        self._csv_logger.close_all()
        
        self._log("All devices stopped.")
        self._print_final_summary()

    def start_control_only(self) -> None:
        """Start configured stimulation control plane (used for replay/simulation)."""
        self._start_control_plane()

    def stop_control_only(self) -> None:
        """Stop configured stimulation control plane (used for replay/simulation)."""
        self._stop_control_plane()
    
    def get_all_stats(self) -> List[DeviceStats]:
        """Get statistics for all devices."""
        with self._lock:
            return list(self._device_stats.values())
    
    def print_status(self) -> None:
        """Print current status of all devices."""
        print("\n" + "=" * 60)
        print("Device Status")
        print("-" * 60)
        
        for name, stats in self._device_stats.items():
            device = self._devices.get(name)
            state_str = stats.state.name if stats.state else "UNKNOWN"
            last_time = stats.last_reading_time.strftime("%H:%M:%S") if stats.last_reading_time else "Never"
            
            print(f"  {name}: {state_str} | "
                  f"Readings: {stats.readings_received} | "
                  f"Errors: {stats.errors} | "
                  f"Last: {last_time}")
        
        print("=" * 60 + "\n")
    
    # endregion
    
    # region Private Methods
    
    def _handle_device_data(self, device_name: str, raw_data: str) -> None:
        """
        Handle incoming data from a device.
        
        Args:
            device_name: Name of the device that sent the data
            raw_data: Raw data string from the device
        """
        timestamp = datetime.now()
        
        # Parse the data
        readings = self._parser.parse(raw_data, timestamp)
        
        for reading in readings:
            reading.device_name = device_name
            # Log to CSV (simplified format: DateTime, UID, Temperature, Zone)
            self._csv_logger.log_reading(
                device_name=device_name,
                timestamp=reading.timestamp,
                zone=reading.zone,
                animal_id=reading.animal_id,
                temperature=reading.temperature
            )
            
            # Update animal registry
            self._registry.record_reading(
                animal_id=reading.animal_id,
                temperature=reading.temperature,
                zone=reading.zone,
                packet_number=reading.packet_number,
                timestamp=reading.timestamp,
                device_name=device_name,
            )
            
            # Update stats
            with self._lock:
                if device_name in self._device_stats:
                    self._device_stats[device_name].readings_received += 1
                    self._device_stats[device_name].last_reading_time = timestamp
                    self._device_stats[device_name].last_zone = reading.zone
            
            # Fire callback
            if self.on_reading_received:
                self.on_reading_received(device_name, reading)
            
            # Console output for each reading (only in non-quiet mode)
            self._log(f"[{timestamp.strftime('%H:%M:%S')}] [{device_name}] {reading}")
    
    def _handle_device_error(self, device_name: str, error: Exception) -> None:
        """
        Handle an error from a device.
        
        Args:
            device_name: Name of the device that had an error
            error: The exception that occurred
        """
        with self._lock:
            if device_name in self._device_stats:
                self._device_stats[device_name].errors += 1
                self._device_stats[device_name].state = DeviceState.ERROR
        
        if self.on_device_error:
            self.on_device_error(device_name, error)
    
    def _update_device_state(self, device_name: str, state: DeviceState) -> None:
        """Update the tracked state of a device including state change time."""
        with self._lock:
            if device_name in self._device_stats:
                old_state = self._device_stats[device_name].state
                self._device_stats[device_name].state = state
                
                # Track when state changed
                if old_state != state:
                    self._device_stats[device_name].last_state_change_time = datetime.now()
                
                # Calculate next reconnect time for disconnected/error states
                if state in (DeviceState.DISCONNECTED, DeviceState.ERROR, DeviceState.RECONNECTING):
                    from datetime import timedelta
                    self._device_stats[device_name].next_reconnect_time = (
                        datetime.now() + timedelta(seconds=self._reconnect_delay_seconds)
                    )
                else:
                    self._device_stats[device_name].next_reconnect_time = None
    
    def _start_health_monitor(self) -> None:
        """Start the background health monitor thread."""
        self._stop_health_monitor.clear()
        self._health_monitor_thread = threading.Thread(
            target=self._health_monitor_loop,
            daemon=True,
            name="DeviceManager-HealthMonitor"
        )
        self._health_monitor_thread.start()
        self._log(f"Health monitor started (checking every {self._health_check_interval_seconds}s, " 
              f"stale timeout: {self._stale_timeout_seconds}s)")

    def _start_trigger_scheduler(self) -> None:
        """Start the trigger scheduler if enabled in config."""
        if self._trigger_schedulers:
            return
        rules = list(self._closed_loop_config.get("rules", []))
        if not rules:
            return
        started = 0
        for rule in rules:
            classifier_cfg = rule.get("classifier", {})
            assigned_ids = [
                str(animal_id).strip().upper()
                for animal_id in rule.get("assigned_animal_ids", [])
                if str(animal_id).strip()
            ]
            if not assigned_ids:
                self._log(
                    f"Closed-loop rule '{rule.get('id', '?')}' disabled: no assigned RFIDs. "
                    "Assign at least one RFID during preflight."
                )
                continue
            try:
                classifier = load_classifier(classifier_cfg["plugin"])
            except Exception as exc:
                self._log(f"Closed-loop rule '{rule.get('id', '?')}' disabled: {exc}")
                continue
            resolved_devices = self._resolve_rule_device_names(rule.get("devices", []))
            scheduler = TriggerScheduler(
                registry=self._registry,
                interval_seconds=float(classifier_cfg["evaluate_interval_seconds"]),
                window_seconds=float(classifier_cfg["clf_data_input_window_seconds"]),
                missing_animal_seconds=float(classifier_cfg.get("missing_animal_seconds") or 0.0),
                classifier=classifier,
                trigger_mode=str(classifier_cfg.get("mode", "window")),
                classifier_config=classifier_cfg.get("config", {}),
                rule_id=str(rule.get("id", "")),
                device_names=resolved_devices,
                target_channels=rule.get("outputs", {}).get("laser_channels", []),
                assigned_animal_ids=assigned_ids,
                on_trigger=self._handle_trigger_event,
                on_missing=self._handle_missing_animal,
                quiet_mode=self._quiet_mode,
            )
            scheduler.start()
            self._trigger_schedulers.append(scheduler)
            started += 1
        if started:
            self._log(f"Closed-loop trigger schedulers started ({started} rule(s)).")

    def _stop_trigger_scheduler(self) -> None:
        """Stop the trigger scheduler if running."""
        for scheduler in self._trigger_schedulers:
            scheduler.stop()
        self._trigger_schedulers = []

    def _resolve_rule_device_names(self, tokens: List[Any]) -> List[str]:
        resolved: List[str] = []
        seen: set[str] = set()
        for token in tokens:
            raw = str(token).strip()
            if not raw:
                continue
            canonical = self._device_identity_map.get(raw, raw)
            if canonical in seen:
                continue
            seen.add(canonical)
            resolved.append(canonical)
        return resolved

    def _start_control_plane(self) -> None:
        """Start stimulus plus the configured control plane."""
        self._start_stimulus()
        if self._control_mode == "open_loop":
            self._start_open_loop_scheduler()
            return
        self._start_trigger_scheduler()

    def _stop_control_plane(self) -> None:
        """Stop open-loop/trigger schedulers and then stop stimulus."""
        self._stop_open_loop_scheduler()
        self._stop_trigger_scheduler()
        self._stop_stimulus()

    def _start_open_loop_scheduler(self) -> None:
        """Start open-loop finite run scheduler if configured."""
        if self._open_loop_thread and self._open_loop_thread.is_alive():
            return
        if self._control_mode != "open_loop":
            return
        if not self._stimulus_config.get("enabled"):
            return
        target_channels = self._get_open_loop_target_channels()
        if not target_channels:
            self._set_open_loop_status(state="error", last_error="No target channels configured.")
            return

        self._stop_open_loop_event.clear()
        self._set_open_loop_status(
            state="scheduled",
            last_error=None,
            pulses_sent=0,
            last_event_time=None,
            launch_confirmed_at=self._live_state.launch_confirmed_at,
            run_started_at=None,
            run_ends_at=None,
            assignments=self._get_open_loop_assignments(),
        )
        self._open_loop_thread = threading.Thread(
            target=self._open_loop_loop,
            daemon=True,
            name="OpenLoopScheduler",
        )
        self._open_loop_thread.start()
        self._log("Open-loop scheduler started.")

    def _stop_open_loop_scheduler(self) -> None:
        """Stop open-loop scheduler if running."""
        self._stop_open_loop_event.set()
        if self._open_loop_thread and self._open_loop_thread.is_alive():
            self._open_loop_thread.join(timeout=2.0)
        self._open_loop_thread = None
        status = self.get_open_loop_status()
        if status["state"] not in ("completed", "error"):
            self._set_open_loop_status(state="stopped")

    def _set_open_loop_status(self, **updates: Any) -> None:
        with self._open_loop_lock:
            for key, value in updates.items():
                if hasattr(self._open_loop_status, key):
                    setattr(self._open_loop_status, key, value)

    def _open_loop_loop(self) -> None:
        """Run open-loop stimulation for a finite window."""
        channels = self._get_open_loop_target_channels()
        run_for_minutes = float(self._open_loop_cfg.get("run_for_minutes", 0.0))
        try:
            start_delay_seconds, scheduled_start_at = self._resolve_open_loop_start(datetime.now())
        except Exception as exc:
            self._set_open_loop_status(state="error", last_error=str(exc))
            return
        run_seconds = max(0.0, run_for_minutes * 60.0)
        train_cfg = self._stimulus_config.get("train", {})
        on_seconds = float(train_cfg.get("on_seconds", self._stimulus_config.get("window_on_seconds", 1.0)))
        off_seconds = float(train_cfg.get("off_seconds", 0.0))

        if run_seconds <= 0:
            self._set_open_loop_status(state="error", last_error="run_for_minutes must be > 0")
            return
        if on_seconds <= 0 or off_seconds < 0:
            self._set_open_loop_status(state="error", last_error="Invalid train.on_seconds/off_seconds values")
            return

        start_cfg = self._open_loop_cfg.get("start") or {}
        if self._open_loop_cfg.get("start_delay_seconds") is not None:
            start_mode = "delay"
        else:
            start_mode = str(start_cfg.get("mode", "immediate")).strip().lower()
        self._set_open_loop_status(
            start_mode=start_mode,
            start_delay_seconds=start_delay_seconds,
            scheduled_start_at=scheduled_start_at,
        )
        if start_delay_seconds > 0:
            if self._stop_open_loop_event.wait(start_delay_seconds):
                self._set_open_loop_status(state="stopped")
                return

        started_at = datetime.now()
        ends_at = datetime.fromtimestamp(started_at.timestamp() + run_seconds)
        self._set_open_loop_status(state="running", run_started_at=started_at, run_ends_at=ends_at)

        try:
            if off_seconds == 0:
                self._emit_open_loop_event("start", channels, "open_loop start")
                if not self._stop_open_loop_event.wait(run_seconds):
                    self._emit_open_loop_event("stop", channels, "open_loop end")
            else:
                deadline = time.monotonic() + run_seconds
                while not self._stop_open_loop_event.is_set():
                    now = time.monotonic()
                    if now >= deadline:
                        break
                    self._emit_open_loop_event("start", channels, "open_loop cycle_start")
                    remaining = deadline - now
                    if self._stop_open_loop_event.wait(min(on_seconds, remaining)):
                        self._emit_open_loop_event("stop", channels, "open_loop interrupted")
                        self._set_open_loop_status(state="stopped")
                        return
                    self._emit_open_loop_event("stop", channels, "open_loop cycle_stop")
                    remaining_after = deadline - time.monotonic()
                    if remaining_after <= 0:
                        break
                    if self._stop_open_loop_event.wait(min(off_seconds, remaining_after)):
                        self._set_open_loop_status(state="stopped")
                        return

            if self._stop_open_loop_event.is_set():
                self._set_open_loop_status(state="stopped")
            else:
                self._set_open_loop_status(state="completed")
        except Exception as exc:
            self._set_open_loop_status(state="error", last_error=str(exc))
            self._log(f"Open-loop scheduler error: {exc}")

    def _emit_open_loop_event(self, action: str, channels: List[str], reason: str) -> None:
        assignments = self._get_open_loop_assignments()
        event = TriggerEvent(
            animal_event_id=None,
            animal_id="__open_loop__",
            rule_id="__open_loop__",
            action=action,
            stimulus_id="__open_loop__",
            reason=reason,
            meta={"source": "open_loop", "channels": list(channels), "assignments": assignments},
            timestamp=datetime.now(),
        )
        self._handle_trigger_event(event)
        with self._open_loop_lock:
            self._open_loop_status.last_event_time = event.timestamp
            if action == "start":
                self._open_loop_status.pulses_sent += 1

    def _start_stimulus(self) -> None:
        if self._stim_controller:
            return
        if not self._stimulus_config.get("enabled"):
            return
        stimulus_cfg = dict(self._stimulus_config)
        active_channels = resolve_active_stimulus_channels(
            {
                "stimulus": self._stimulus_config,
                "closed_loop": self._closed_loop_config,
            }
        )
        if active_channels:
            stimulus_cfg["channels"] = active_channels
        self._stim_controller = StimulationController.from_config(
            stimulus_cfg, quiet_mode=self._quiet_mode, live_state=self._live_state
        )
        self._stim_fault_reported = False
        started = self._stim_controller.start()
        if started:
            if not self._quiet_mode:
                self._log(f"Stimulus controller ready ({self._stim_controller.describe()}).")
            return
        status = self._stim_controller.status()
        self._log(
            "Stimulus controller initialization failed; continuing acquisition in fail-closed mode. "
            f"fault={status.get('fault_reason')}"
        )

    def _stop_stimulus(self) -> None:
        if self._stim_controller:
            status = self._stim_controller.status()
            if status.get("enabled"):
                open_loop_status = self.get_open_loop_status()
                if (
                    self._control_mode == "open_loop"
                    and open_loop_status.get("state") in ("scheduled", "running")
                    and open_loop_status.get("target_channels")
                ):
                    self._record_trigger_event(
                        TriggerEvent(
                            animal_event_id=None,
                            animal_id="__open_loop__",
                            rule_id="__open_loop__",
                            action="stop",
                            stimulus_id="__open_loop__",
                            reason="cleanup open_loop",
                            meta={
                                "source": "device_manager.stop",
                                "channels": list(open_loop_status.get("target_channels", [])),
                            },
                            timestamp=datetime.now(),
                        )
                    )
                trigger_statuses = self.get_trigger_statuses()
                for _, trigger_status in trigger_statuses.items():
                    if trigger_status.condition_true:
                        self._record_trigger_event(
                            TriggerEvent(
                                animal_event_id=None,
                                animal_id=trigger_status.animal_id,
                                rule_id=trigger_status.rule_id,
                                action="stop",
                                stimulus_id=trigger_status.stimulus_id,
                                reason="cleanup active_window",
                                meta={
                                    "source": "device_manager.stop",
                                    "channels": list(trigger_status.target_channels),
                                    "rule_id": trigger_status.rule_id,
                                },
                                timestamp=datetime.now(),
                            )
                        )
                self._record_trigger_event(
                    TriggerEvent(
                        animal_event_id=None,
                        animal_id="__system__",
                        rule_id="__cleanup__",
                        action="stop",
                        stimulus_id="__cleanup__",
                        reason="cleanup shutdown stop_all",
                        meta={"source": "device_manager.stop"},
                        timestamp=datetime.now(),
                    )
                )
            self._stim_controller.stop()
            self._stim_controller = None
            self._live_state.laser_driver = None

    def _handle_trigger_event(self, event: TriggerEvent) -> None:
        """Handle a trigger decision (hardware hook placeholder)."""
        self._record_trigger_event(event)
        if self._stim_controller:
            self._stim_controller.handle_trigger_event(event)
            status = self._stim_controller.status()
            if status.get("state") == StimulationController.STATE_FAULT and not self._stim_fault_reported:
                self._stim_fault_reported = True
                self._log(
                    "Stimulus controller entered FAULT; outputs are disabled until restart/re-init. "
                    f"fault={status.get('fault_reason')}"
                )
        if not self._quiet_mode:
            self._log(
                f"[Trigger] rule={event.rule_id or '-'} animal={event.animal_id} action={event.action} "
                f"stimulus={event.stimulus_id} reason='{event.reason}'"
            )

    def _handle_missing_animal(self, rule_id: str, animal_id: str, seconds_since: float) -> None:
        """Handle missing-animal alarm."""
        self._missing_events.append(
            {"rule_id": rule_id, "animal_id": animal_id, "seconds_since": seconds_since, "timestamp": datetime.now()}
        )
        if not self._quiet_mode:
            self._log(f"[Missing] rule={rule_id} animal={animal_id} last seen {seconds_since:.0f}s ago")

    def get_trigger_events(self) -> List[TriggerEvent]:
        """Return collected trigger events."""
        return list(self._trigger_events)

    def get_missing_events(self) -> List[Dict[str, Any]]:
        """Return collected missing-animal events."""
        return list(self._missing_events)

    def get_stimulus_status(self) -> Dict[str, Any]:
        """Return stimulation status for diagnostics/UI."""
        if not self._stimulus_config.get("enabled"):
            return {"enabled": False, "state": StimulationController.STATE_DISABLED}
        if not self._stim_controller:
            return {
                "enabled": True,
                "mode": str(self._stimulus_config.get("mode", "monitor")),
                "state": "not_started",
                "fault_reason": None,
            }
        return self._stim_controller.status()

    def get_trigger_statuses(self) -> Dict[str, TriggerAnimalStatus]:
        """Return live trigger evaluation status by animal."""
        merged: Dict[str, TriggerAnimalStatus] = {}
        for scheduler in self._trigger_schedulers:
            merged.update(scheduler.get_status_snapshot())
        return merged

    def get_open_loop_status(self) -> Dict[str, Any]:
        """Return live open-loop scheduler status."""
        with self._open_loop_lock:
            status = self._open_loop_status
            return {
                "enabled": status.enabled,
                "state": status.state,
                "control_mode": status.control_mode,
                "target_channels": list(status.target_channels),
                "assignments": [dict(item) for item in status.assignments],
                "run_for_minutes": status.run_for_minutes,
                "start_mode": status.start_mode,
                "start_delay_seconds": status.start_delay_seconds,
                "scheduled_start_at": status.scheduled_start_at,
                "launch_confirmed_at": status.launch_confirmed_at,
                "run_started_at": status.run_started_at,
                "run_ends_at": status.run_ends_at,
                "pulses_sent": status.pulses_sent,
                "last_event_time": status.last_event_time,
                "last_error": status.last_error,
            }

    def _get_open_loop_assignments(self) -> List[Dict[str, Any]]:
        assignments = self._open_loop_cfg.get("open_loop_assignments", [])
        if not isinstance(assignments, list):
            return []
        normalized: List[Dict[str, Any]] = []
        for item in assignments:
            if not isinstance(item, dict):
                continue
            assignment_id = str(item.get("id") or "").strip()
            channel = str(item.get("channel") or "").strip()
            if not assignment_id or not channel:
                continue
            normalized.append(
                {
                    "id": assignment_id,
                    "channel": channel,
                    "assigned_animal_ids": [
                        str(animal_id).strip().upper()
                        for animal_id in item.get("assigned_animal_ids", [])
                        if str(animal_id).strip()
                    ],
                }
            )
        return normalized

    def _get_open_loop_target_channels(self) -> List[str]:
        assignments = self._get_open_loop_assignments()
        if assignments:
            return [str(item["channel"]) for item in assignments]
        return [str(ch) for ch in self._open_loop_cfg.get("target_channels", []) if str(ch).strip()]

    def _resolve_open_loop_start(self, launch_time: datetime) -> tuple[float, Optional[datetime]]:
        start_cfg = self._open_loop_cfg.get("start") or {}
        if self._open_loop_cfg.get("start_delay_seconds") is not None:
            start_cfg = {
                "mode": "delay",
                "delay_seconds": self._open_loop_cfg.get("start_delay_seconds"),
            }
        mode = str(start_cfg.get("mode", "immediate")).strip().lower()
        if mode == "delay":
            delay_seconds = float(start_cfg.get("delay_seconds", 0.0) or 0.0)
            return delay_seconds, launch_time + timedelta(seconds=delay_seconds)
        if mode == "clock":
            timezone_name = str(start_cfg.get("timezone") or "").strip()
            at_hhmm = str(start_cfg.get("at_hhmm") or "").strip()
            rollback_next_day = bool(start_cfg.get("rollback_next_day", False))
            if not timezone_name or not at_hhmm:
                raise ValueError("stimulus.start clock mode requires timezone and at_hhmm.")
            try:
                zone = ZoneInfo(timezone_name)
            except Exception as exc:
                raise ValueError(f"Unknown stimulus.start.timezone '{timezone_name}'.") from exc
            try:
                hour_text, minute_text = at_hhmm.split(":", 1)
                hour = int(hour_text)
                minute = int(minute_text)
            except Exception as exc:
                raise ValueError("stimulus.start.at_hhmm must match HH:MM in 24-hour time.") from exc
            if launch_time.tzinfo is None:
                launch_local = launch_time.replace(tzinfo=zone)
            else:
                launch_local = launch_time.astimezone(zone)
            scheduled_local = launch_local.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if scheduled_local < launch_local:
                if not rollback_next_day:
                    raise ValueError(
                        "stimulus.start.at_hhmm is earlier than launch time and rollback_next_day is false."
                    )
                scheduled_local = scheduled_local + timedelta(days=1)
            scheduled_start_at = scheduled_local
            delay_seconds = max(0.0, (scheduled_start_at - launch_local).total_seconds())
            return delay_seconds, scheduled_start_at
        return 0.0, launch_time

    def get_ttl_capture_status(self) -> Dict[str, Any]:
        """Return TTL capture status for diagnostics/UI."""
        if not self._ttl_capture:
            return {"enabled": False, "running": False}
        status = self._ttl_capture.status_dict()
        status["enabled"] = True
        return status

    def _start_ttl_capture(self) -> None:
        should_start = bool(self._ttl_capture_config.get("enabled", False)) and bool(
            self._stimulus_config.get("square", {}).get("ttl_output", False)
        )
        if not should_start:
            return
        if self._ttl_capture:
            return

        serial_port = str(self._ttl_capture_config.get("port", "")).strip()
        if not serial_port:
            self._log("TTL capture skipped: ttl_capture.port not set.")
            return

        try:
            from ttl_capture.capture import TTLCaptureService

            self._ttl_capture = TTLCaptureService(
                session_folder=self._session_folder,
                port=serial_port,
                baudrate=int(self._ttl_capture_config.get("baudrate", 115200)),
                timeout_seconds=float(self._ttl_capture_config.get("serial_timeout_seconds", 0.25)),
                read_chunk_bytes=int(self._ttl_capture_config.get("read_chunk_bytes", 4096)),
            )
            self._ttl_capture.start()
            self._log(f"TTL capture started on {serial_port}.")
        except Exception as exc:
            self._ttl_capture = None
            self._log(f"TTL capture failed to start; continuing without TTL capture: {exc}")

    def _stop_ttl_capture(self) -> None:
        if not self._ttl_capture:
            return
        try:
            self._ttl_capture.stop()
        finally:
            self._ttl_capture = None

    def _record_trigger_event(self, event: TriggerEvent) -> None:
        if event.animal_event_id is None:
            next_id = self._next_animal_event_id.get(event.animal_id, 1)
            event.animal_event_id = next_id
            self._next_animal_event_id[event.animal_id] = next_id + 1
        if not isinstance(event.meta, dict):
            event.meta = {}
        event.meta.setdefault("recorded_monotonic_ns", time.monotonic_ns())
        self._trigger_events.append(event)

    def ingest_reading(self, device_name: str, reading: ParsedReading) -> None:
        """Ingest a parsed reading without device IO (replay/simulation)."""
        # Ensure stats exist for this logical device
        with self._lock:
            if device_name not in self._device_stats:
                self._device_stats[device_name] = DeviceStats(
                    device_name=device_name,
                    state=DeviceState.CONNECTED,
                    readings_received=0,
                    last_reading_time=None,
                    errors=0,
                    last_state_change_time=datetime.now(),
                    next_reconnect_time=None,
                )
        
        # Log to CSV
        self._csv_logger.log_reading(
            device_name=device_name,
            timestamp=reading.timestamp,
            zone=reading.zone,
            animal_id=reading.animal_id,
            temperature=reading.temperature
        )
        
        # Update animal registry
        self._registry.record_reading(
            animal_id=reading.animal_id,
            temperature=reading.temperature,
            zone=reading.zone,
            packet_number=reading.packet_number,
            timestamp=reading.timestamp,
            device_name=device_name,
        )
        
        with self._lock:
            if device_name in self._device_stats:
                self._device_stats[device_name].readings_received += 1
                self._device_stats[device_name].last_reading_time = reading.timestamp
                self._device_stats[device_name].last_zone = reading.zone
    
    def _stop_health_monitor_thread(self) -> None:
        """Stop the health monitor thread."""
        self._stop_health_monitor.set()
        if self._health_monitor_thread and self._health_monitor_thread.is_alive():
            self._health_monitor_thread.join(timeout=2.0)
    
    def _health_monitor_loop(self) -> None:
        """
        Background loop that monitors device health and triggers reconnections.
        
        Checks each device for:
        1. Connection failures (not connected) - will reconnect
        2. Stale connections (no data in stale_timeout_seconds) - will force reconnect
        """
        while not self._stop_health_monitor.is_set():
            # Wait for the check interval
            self._stop_health_monitor.wait(self._health_check_interval_seconds)
            
            if self._stop_health_monitor.is_set():
                break
            
            # Check each device
            with self._lock:
                devices_to_check = list(self._devices.items())
            
            for device_name, device in devices_to_check:
                try:
                    # Check if device is stale (connected but no data)
                    if device.is_stale:
                        seconds = device.seconds_since_last_data
                        self._log(f"[HealthMonitor] [{device_name}] No data for {seconds:.0f}s - forcing reconnect")
                        self._update_device_state(device_name, DeviceState.RECONNECTING)
                        device.force_reconnect()
                    
                    # Check if device is disconnected/errored and not already reconnecting
                    elif device.state in (DeviceState.DISCONNECTED, DeviceState.ERROR):
                        self._log(f"[HealthMonitor] [{device_name}] Device in {device.state.name} state - attempting reconnect")
                        self._update_device_state(device_name, DeviceState.RECONNECTING)
                        
                        # The receive thread should handle reconnection, but if it's not running, restart it
                        if not device._receive_thread or not device._receive_thread.is_alive():
                            if device.connect():
                                device.send_command("RRLOOP")
                                device.start_receiving(auto_reconnect=True)
                                self._update_device_state(device_name, DeviceState.CONNECTED)
                            else:
                                self._log(f"[HealthMonitor] [{device_name}] Reconnection failed, will retry in {self._reconnect_delay_seconds}s")
                    
                    # Update state tracking for connected devices
                    elif device.is_connected:
                        self._update_device_state(device_name, DeviceState.CONNECTED)
                        
                except Exception as e:
                    self._log(f"[HealthMonitor] [{device_name}] Error during health check: {e}")
    
    def _print_final_summary(self) -> None:
        """Print a summary when stopping."""
        print("\n" + "=" * 60)
        print("Session Summary")
        print("-" * 60)
        
        total_readings = sum(s.readings_received for s in self._device_stats.values())
        total_errors = sum(s.errors for s in self._device_stats.values())
        
        print(f"  Total readings received: {total_readings}")
        print(f"  Total errors: {total_errors}")
        print(f"  Animals tracked: {self._registry.animal_count}")
        print(f"  Parser success rate: {self._parser.success_rate:.1%}")
        
        summary = self._registry.get_summary()
        if summary["avg_temperature"] is not None:
            print(f"  Avg temperature: {summary['avg_temperature']:.2f}°C")
            print(f"  Temp range: {summary['min_temperature']:.2f}°C - {summary['max_temperature']:.2f}°C")
        
        print("=" * 60 + "\n")
    
    # endregion
    
    # region Context Manager Support
    
    def __enter__(self) -> 'DeviceManager':
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop_all()
    
    # endregion
    
    def __repr__(self) -> str:
        return f"DeviceManager(devices={self.device_count}, running={self._is_running})"

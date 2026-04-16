"""
IP Device module - Handles network connections to serial-over-IP devices.

Provides a clean abstraction for connecting to devices, sending commands,
and receiving data with proper error handling and reconnection logic.
"""

import socket
import threading
import time
from typing import Optional, Callable, Any
from dataclasses import dataclass
from enum import Enum, auto
from datetime import datetime


class DeviceState(Enum):
    """Represents the connection state of a device."""
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    ERROR = auto()
    RECONNECTING = auto()


@dataclass
class DeviceConfig:
    """
    Configuration for an IP device connection.
    
    Attributes:
        host: IP address or hostname of the device
        port: TCP port number
        device_name: Human-readable name for logging
        timeout_seconds: Socket timeout for receive operations
        buffer_size: Size of the receive buffer
        reconnect_delay_seconds: Delay between reconnection attempts
        max_reconnect_attempts: Maximum number of reconnection attempts (0 = infinite)
        stale_timeout_seconds: Time without data before considering connection stale
    """
    host: str
    port: int
    device_name: str = "Device"
    timeout_seconds: float = 5.0
    buffer_size: int = 1024
    reconnect_delay_seconds: float = 30.0  # Retry every 30 seconds
    max_reconnect_attempts: int = 0  # 0 = infinite
    stale_timeout_seconds: float = 30.0  # Reconnect if no data for 30 seconds


class IPDevice:
    """
    Represents a network-connected serial device.
    
    Handles connection management, command sending, and data reception
    with automatic reconnection support for 24/7 operation.
    
    Example:
        >>> config = DeviceConfig(host="192.168.1.100", port=10001, device_name="Reader-1")
        >>> device = IPDevice(config)
        >>> device.on_data_received = lambda data: print(f"Got: {data}")
        >>> device.connect()
        >>> device.send_command("RRLOOP")
        >>> device.start_receiving()
    """
    
    def __init__(self, config: DeviceConfig, quiet_mode: bool = False):
        """
        Initialize a new IPDevice instance.
        
        Args:
            config: Device configuration settings
            quiet_mode: If True, suppresses console output (for use with GUI)
        """
        self._config = config
        self._quiet_mode = quiet_mode
        self._socket: Optional[socket.socket] = None
        self._state = DeviceState.DISCONNECTED
        self._receive_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._reconnect_attempts = 0
        self._last_data_time: Optional[datetime] = None
        
        # Event callbacks (similar to C# events)
        self.on_data_received: Optional[Callable[[str], None]] = None
        self.on_state_changed: Optional[Callable[[DeviceState], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None
        self.on_connected: Optional[Callable[[], None]] = None
        self.on_disconnected: Optional[Callable[[], None]] = None
    
    # region Properties
    
    @property
    def config(self) -> DeviceConfig:
        """Gets the device configuration."""
        return self._config
    
    @property
    def state(self) -> DeviceState:
        """Gets the current connection state."""
        return self._state
    
    @property
    def is_connected(self) -> bool:
        """Returns True if the device is currently connected."""
        return self._state == DeviceState.CONNECTED
    
    @property
    def device_name(self) -> str:
        """Gets the human-readable device name."""
        return self._config.device_name
    
    @property
    def last_data_time(self) -> Optional[datetime]:
        """Gets the timestamp of the last received data."""
        return self._last_data_time
    
    @property
    def connection_string(self) -> str:
        """Gets a formatted connection string for display."""
        return f"{self._config.host}:{self._config.port}"
    
    @property
    def seconds_since_last_data(self) -> Optional[float]:
        """Gets the number of seconds since last data was received, or None if never."""
        if self._last_data_time is None:
            return None
        return (datetime.now() - self._last_data_time).total_seconds()
    
    @property
    def is_stale(self) -> bool:
        """
        Returns True if the device hasn't received data within the stale timeout.
        
        A device is considered stale if:
        - It's connected but hasn't received data in stale_timeout_seconds
        - It was receiving data but stopped
        """
        if not self.is_connected:
            return False
        
        seconds = self.seconds_since_last_data
        if seconds is None:
            # Never received data - check if we've been connected long enough
            return False
        
        return seconds > self._config.stale_timeout_seconds
    
    # endregion
    
    # region Public Methods
    
    def connect(self) -> bool:
        """
        Establish a connection to the device.
        
        Returns:
            True if connection was successful, False otherwise.
        """
        if self._state == DeviceState.CONNECTED:
            return True
        
        self._set_state(DeviceState.CONNECTING)
        
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(self._config.timeout_seconds)
            self._socket.connect((self._config.host, self._config.port))
            
            self._set_state(DeviceState.CONNECTED)
            self._reconnect_attempts = 0
            
            self._log(f"Connected to {self.connection_string}")
            
            if self.on_connected:
                self.on_connected()
            
            return True
            
        except socket.error as e:
            self._handle_error(e)
            return False
    
    def disconnect(self) -> None:
        """Disconnect from the device and stop all operations."""
        self._stop_event.set()
        
        if self._receive_thread and self._receive_thread.is_alive():
            self._receive_thread.join(timeout=2.0)
        
        self._close_socket()
        self._set_state(DeviceState.DISCONNECTED)
        
        self._log("Disconnected")
        
        if self.on_disconnected:
            self.on_disconnected()
    
    def send_command(self, command: str, append_crlf: bool = True) -> bool:
        """
        Send a command to the device.
        
        Args:
            command: The command string to send
            append_crlf: Whether to append \\r\\n to the command
        
        Returns:
            True if the command was sent successfully, False otherwise.
        """
        if not self.is_connected or self._socket is None:
            self._log("Cannot send command: not connected")
            return False
        
        try:
            if append_crlf and not command.endswith("\r\n"):
                command = command + "\r\n"
            
            self._socket.sendall(command.encode('ascii'))
            self._log(f"Sent command: {command.strip()}")
            return True
            
        except socket.error as e:
            self._handle_error(e)
            return False
    
    def start_receiving(self, auto_reconnect: bool = True) -> None:
        """
        Start the background thread for receiving data.
        
        Args:
            auto_reconnect: Whether to automatically reconnect on connection loss
        """
        if self._receive_thread and self._receive_thread.is_alive():
            self._log("Receive thread already running")
            return
        
        self._stop_event.clear()
        self._receive_thread = threading.Thread(
            target=self._receive_loop,
            args=(auto_reconnect,),
            daemon=True,
            name=f"IPDevice-{self._config.device_name}-Receiver"
        )
        self._receive_thread.start()
        self._log("Started receiving data")
    
    def stop_receiving(self) -> None:
        """Stop the background receive thread."""
        self._stop_event.set()
        if self._receive_thread and self._receive_thread.is_alive():
            self._receive_thread.join(timeout=2.0)
        self._log("Stopped receiving data")
    
    # endregion
    
    # region Private Methods
    
    def _receive_loop(self, auto_reconnect: bool) -> None:
        """
        Main receive loop running in a background thread.
        
        Args:
            auto_reconnect: Whether to attempt reconnection on failure
        """
        while not self._stop_event.is_set():
            if not self.is_connected:
                if auto_reconnect:
                    self._attempt_reconnect()
                else:
                    break
                continue
            
            try:
                if self._socket is None:
                    continue
                    
                data = self._socket.recv(self._config.buffer_size)
                
                if not data:
                    # Connection closed by remote host
                    self._log("Connection closed by remote host")
                    self._set_state(DeviceState.DISCONNECTED)
                    continue
                
                # Decode and process data
                decoded_data = data.decode('ascii', errors='replace').strip()
                self._last_data_time = datetime.now()
                
                # Fire the data received callback
                if self.on_data_received and decoded_data:
                    self.on_data_received(decoded_data)
                    
            except socket.timeout:
                # No data within timeout - this is normal, just continue
                continue
                
            except socket.error as e:
                self._handle_error(e)
                if not auto_reconnect:
                    break
    
    def _attempt_reconnect(self) -> None:
        """Attempt to reconnect to the device with backoff."""
        max_attempts = self._config.max_reconnect_attempts
        
        if max_attempts > 0 and self._reconnect_attempts >= max_attempts:
            self._log(f"Max reconnection attempts ({max_attempts}) reached")
            self._set_state(DeviceState.ERROR)
            return
        
        self._set_state(DeviceState.RECONNECTING)
        self._reconnect_attempts += 1
        
        self._log(f"Reconnection attempt {self._reconnect_attempts}...")
        
        # Wait before attempting reconnection
        self._stop_event.wait(self._config.reconnect_delay_seconds)
        
        if self._stop_event.is_set():
            return
        
        self._close_socket()
        
        if self.connect():
            # Re-send the startup command after reconnection
            self.send_command("RRLOOP")
    
    def _close_socket(self) -> None:
        """Safely close the socket connection."""
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
    
    def force_reconnect(self) -> None:
        """
        Force a reconnection by closing the current connection.
        
        The receive loop will detect the disconnection and attempt to reconnect.
        Use this when the connection appears stale (no data received).
        """
        self._log("Forcing reconnection due to stale connection...")
        self._close_socket()
        self._set_state(DeviceState.DISCONNECTED)
        self._last_data_time = None  # Reset so we don't immediately think it's stale again
    
    def _set_state(self, new_state: DeviceState) -> None:
        """
        Update the device state and fire the state changed callback.
        
        Args:
            new_state: The new device state
        """
        if self._state != new_state:
            self._state = new_state
            if self.on_state_changed:
                self.on_state_changed(new_state)
    
    def _handle_error(self, error: Exception) -> None:
        """
        Handle an error and fire the error callback.
        
        Args:
            error: The exception that occurred
        """
        self._log(f"Error: {error}")
        self._set_state(DeviceState.ERROR)
        
        if self.on_error:
            self.on_error(error)
    
    def _log(self, message: str) -> None:
        """
        Log a message with device context.
        
        Args:
            message: The message to log
        """
        if not self._quiet_mode:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] [{self._config.device_name}] {message}")
    
    # endregion
    
    # region Context Manager Support (Python's equivalent to C# IDisposable)
    
    def __enter__(self) -> 'IPDevice':
        """Support for 'with' statement - connect on enter."""
        self.connect()
        return self
    
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Support for 'with' statement - disconnect on exit."""
        self.disconnect()
    
    # endregion
    
    def __repr__(self) -> str:
        return f"IPDevice(name='{self._config.device_name}', host='{self._config.host}', state={self._state.name})"

"""
Animal module - Represents a tracked animal with temperature history.

Each animal is identified by a unique ID and maintains a rolling 1-minute
average of temperature readings.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from collections import deque


@dataclass
class TemperatureReading:
    """Represents a single temperature reading with timestamp."""
    timestamp: datetime
    temperature: float
    zone: int
    packet_number: int
    device_name: Optional[str] = None


class Animal:
    """
    Represents a tracked animal with temperature history.
    
    Maintains a rolling window of temperature readings for computing
    1-minute averages. Thread-safe for concurrent access.
    
    Attributes:
        animal_id (str): Unique identifier for the animal (e.g., 'ABCD1234')
        readings (deque): Rolling window of TemperatureReading objects
        averaging_window_seconds (int): Window size for rolling average (default 60s)
    
    Example:
        >>> animal = Animal("ABCD1234")
        >>> animal.add_reading(28.5, zone=8, packet_number=2474)
        >>> print(animal.average_temperature)
        28.5
    """
    
    # Class constant for the averaging window
    DEFAULT_AVERAGING_WINDOW_SECONDS = 60
    
    def __init__(self, animal_id: str, averaging_window_seconds: int = DEFAULT_AVERAGING_WINDOW_SECONDS):
        """
        Initialize a new Animal instance.
        
        Args:
            animal_id: Unique identifier for this animal
            averaging_window_seconds: Time window for rolling average calculation
        """
        self._animal_id = animal_id
        self._averaging_window_seconds = averaging_window_seconds
        self._readings: deque[TemperatureReading] = deque()
        self._last_scan_time: Optional[datetime] = None
        self._last_zone: Optional[int] = None
        self._last_device_name: Optional[str] = None
        self._total_readings: int = 0
    
    # region Properties (C# style region for familiarity)
    
    @property
    def animal_id(self) -> str:
        """Gets the unique identifier for this animal."""
        return self._animal_id
    
    @property
    def last_scan_time(self) -> Optional[datetime]:
        """Gets the timestamp of the most recent reading."""
        return self._last_scan_time
    
    @property
    def last_zone(self) -> Optional[int]:
        """Gets the zone where the animal was last scanned."""
        return self._last_zone
    
    @property
    def last_temperature(self) -> Optional[float]:
        """Gets the most recent temperature reading."""
        if not self._readings:
            return None
        return self._readings[-1].temperature

    @property
    def last_device_name(self) -> Optional[str]:
        """Gets the source device name of the most recent reading."""
        return self._last_device_name
    
    @property
    def total_readings(self) -> int:
        """Gets the total number of readings received for this animal."""
        return self._total_readings
    
    @property
    def readings_in_window(self) -> int:
        """Gets the number of readings currently in the averaging window."""
        self._prune_old_readings()
        return len(self._readings)
    
    @property
    def average_temperature(self) -> Optional[float]:
        """
        Computes the rolling average temperature over the averaging window.
        
        Returns:
            The average temperature, or None if no readings exist.
        """
        self._prune_old_readings()
        
        if not self._readings:
            return None
        
        total = sum(reading.temperature for reading in self._readings)
        return total / len(self._readings)
    
    @property
    def min_temperature(self) -> Optional[float]:
        """Gets the minimum temperature in the current window."""
        self._prune_old_readings()
        if not self._readings:
            return None
        return min(reading.temperature for reading in self._readings)
    
    @property
    def max_temperature(self) -> Optional[float]:
        """Gets the maximum temperature in the current window."""
        self._prune_old_readings()
        if not self._readings:
            return None
        return max(reading.temperature for reading in self._readings)
    
    @property
    def seconds_since_last_scan(self) -> Optional[float]:
        """Gets the number of seconds since the last scan, or None if never scanned."""
        if self._last_scan_time is None:
            return None
        return (datetime.now() - self._last_scan_time).total_seconds()
    
    # endregion
    
    # region Public Methods
    
    def add_reading(
        self,
        temperature: float,
        zone: int,
        packet_number: int,
        timestamp: Optional[datetime] = None,
        device_name: Optional[str] = None,
    ) -> None:
        """
        Add a new temperature reading for this animal.
        
        Args:
            temperature: The temperature value
            zone: The zone number where the reading was taken
            packet_number: The packet number from the device
            timestamp: Optional timestamp (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now()
        
        reading = TemperatureReading(
            timestamp=timestamp,
            temperature=temperature,
            zone=zone,
            packet_number=packet_number,
            device_name=device_name,
        )
        
        self._readings.append(reading)
        self._last_scan_time = timestamp
        self._last_zone = zone
        self._last_device_name = device_name
        self._total_readings += 1
        
        # Prune old readings to keep memory bounded
        self._prune_old_readings()
    
    def get_readings_in_window(self) -> List[TemperatureReading]:
        """
        Get all readings within the current averaging window.
        
        Returns:
            List of TemperatureReading objects within the window.
        """
        self._prune_old_readings()
        return list(self._readings)
    
    def to_display_string(self) -> str:
        """
        Generate a formatted string for console display.
        
        Returns:
            Human-readable status string for this animal.
        """
        avg_temp = self.average_temperature
        last_temp = self.last_temperature
        seconds_ago = self.seconds_since_last_scan
        
        avg_str = f"{avg_temp:.2f}°C" if avg_temp is not None else "N/A"
        last_str = f"{last_temp:.2f}°C" if last_temp is not None else "N/A"
        
        if seconds_ago is not None:
            if seconds_ago < 60:
                time_str = f"{seconds_ago:.0f}s ago"
            else:
                time_str = f"{seconds_ago / 60:.1f}m ago"
        else:
            time_str = "Never"
        
        zone_str = f"Zone {self._last_zone}" if self._last_zone is not None else "Unknown"
        
        return (f"[{self._animal_id}] "
                f"Avg: {avg_str} | "
                f"Last: {last_str} | "
                f"Readings: {self.readings_in_window} | "
                f"Location: {zone_str} | "
                f"Last Scan: {time_str}")
    
    # endregion
    
    # region Private Methods
    
    def _prune_old_readings(self) -> None:
        """Remove readings older than the averaging window."""
        cutoff_time = datetime.now() - timedelta(seconds=self._averaging_window_seconds)
        
        while self._readings and self._readings[0].timestamp < cutoff_time:
            self._readings.popleft()
    
    # endregion
    
    # region Magic Methods (Dunder methods - Python's equivalent to C# operator overloads)
    
    def __repr__(self) -> str:
        """Developer-friendly string representation."""
        return f"Animal(id='{self._animal_id}', readings={self._total_readings})"
    
    def __str__(self) -> str:
        """User-friendly string representation."""
        return self.to_display_string()
    
    def __eq__(self, other: object) -> bool:
        """Equality comparison based on animal_id."""
        if not isinstance(other, Animal):
            return NotImplemented
        return self._animal_id == other._animal_id
    
    def __hash__(self) -> int:
        """Hash based on animal_id for use in sets/dicts."""
        return hash(self._animal_id)
    
    # endregion

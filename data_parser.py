"""
Data Parser module - Parses incoming data from temperature monitoring devices.

Handles the specific data format: ";2474;8;ABCD1234,28.9;"
where:
    - 2474 is the packet number
    - 8 is the zone
    - ABCD1234 is the animal ID
    - 28.9 is the temperature
"""

import re
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime


@dataclass
class ParsedReading:
    """
    Represents a successfully parsed temperature reading.
    
    Attributes:
        packet_number: Sequence number of the packet
        zone: Zone number where the reading was taken
        animal_id: Unique identifier for the animal
        temperature: Temperature value in degrees
        raw_data: Original raw data string
        timestamp: When the reading was parsed
    """
    packet_number: int
    zone: int
    animal_id: str
    temperature: float
    raw_data: str
    timestamp: datetime
    device_name: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "packet_number": self.packet_number,
            "zone": self.zone,
            "animal_id": self.animal_id,
            "temperature": self.temperature,
            "raw_data": self.raw_data,
            "timestamp": self.timestamp.isoformat(),
            "device_name": self.device_name,
        }
    
    def __str__(self) -> str:
        return f"Packet {self.packet_number}: Zone {self.zone}, ID={self.animal_id}, Temp={self.temperature}°C"


class DataParser:
    """
    Parses temperature monitoring data from serial devices.
    
    Handles the specific format: ";2474;8;ABCD1234,28.9;"
    Also handles multiple readings in a single data chunk.
    
    Example:
        >>> parser = DataParser()
        >>> readings = parser.parse(";2474;8;ABCD1234,28.9;")
        >>> for reading in readings:
        ...     print(f"Animal {reading.animal_id}: {reading.temperature}°C")
    """
    
    # Regex pattern to match individual readings
    # Format: ;packet_number;zone;animal_id,temperature;
    # Actual device format: \x02;1035;5;2A8F2EF2,<25;\x03 or \x02;1035;5;2A8F2EF2,28.9;\x03
    # The temperature can be:
    #   - A number like 28 or 28.9
    #   - A comparison like <25 or <25.0 or >40 (means below/above threshold)
    # STX (\x02) and ETX (\x03) control characters may wrap the data
    # Note: XXXXXXXX indicates a no-read and should be filtered out
    READING_PATTERN = re.compile(
        r';(\d+);(\d+);([A-Fa-f0-9]+),([<>]?-?\d+\.?\d*);',
        re.IGNORECASE
    )
    
    # Alternative pattern for slightly different formats
    # Some devices might use different delimiters
    ALT_PATTERN = re.compile(
        r';(\d+);(\d+);([A-Fa-f0-9]+)[,;]([<>]?-?\d+\.?\d*);?',
        re.IGNORECASE
    )
    
    # Pattern for no-read indicator (XXXXXXXX or similar)
    NO_READ_PATTERN = re.compile(r'^X+$', re.IGNORECASE)
    
    def __init__(self, strict_mode: bool = False):
        """
        Initialize a new DataParser instance.
        
        Args:
            strict_mode: If True, only accept exact format matches.
                        If False, try alternative patterns.
        """
        self._strict_mode = strict_mode
        self._total_parsed = 0
        self._total_failed = 0
    
    # region Properties
    
    @property
    def total_parsed(self) -> int:
        """Gets the total number of successfully parsed readings."""
        return self._total_parsed
    
    @property
    def total_failed(self) -> int:
        """Gets the total number of failed parse attempts."""
        return self._total_failed
    
    @property
    def success_rate(self) -> float:
        """Gets the parsing success rate (0.0 to 1.0)."""
        total = self._total_parsed + self._total_failed
        if total == 0:
            return 1.0
        return self._total_parsed / total
    
    # endregion
    
    # region Public Methods
    
    def parse(self, raw_data: str, timestamp: Optional[datetime] = None) -> List[ParsedReading]:
        """
        Parse raw data string and extract all temperature readings.
        
        Handles multiple readings concatenated together, which is common
        when receiving buffered data from the device.
        
        Args:
            raw_data: The raw data string from the device
            timestamp: Optional timestamp (defaults to now)
        
        Returns:
            List of ParsedReading objects (may be empty if no valid readings)
        """
        if timestamp is None:
            timestamp = datetime.now()
        
        readings: List[ParsedReading] = []
        
        if not raw_data or not raw_data.strip():
            return readings
        
        # Try primary pattern first
        matches = self.READING_PATTERN.findall(raw_data)
        
        # If no matches and not in strict mode, try alternative pattern
        if not matches and not self._strict_mode:
            matches = self.ALT_PATTERN.findall(raw_data)
        
        for match in matches:
            try:
                animal_id = match[2].upper()  # Normalize to uppercase
                
                # Skip no-read indicators (XXXXXXXX or similar)
                if self.NO_READ_PATTERN.match(animal_id):
                    continue
                
                # Temperature can be a plain number or prefixed with < or >
                # e.g., "28", "28.9", "<25", "<25.0", ">40"
                temp_str = match[3]
                if temp_str.startswith('<') or temp_str.startswith('>'):
                    # Strip the comparison operator, just keep the number
                    temperature = float(temp_str[1:])
                else:
                    temperature = float(temp_str)
                
                reading = ParsedReading(
                    packet_number=int(match[0]),
                    zone=int(match[1]),
                    animal_id=animal_id,
                    temperature=temperature,
                    raw_data=raw_data,
                    timestamp=timestamp
                )
                readings.append(reading)
                self._total_parsed += 1
                
            except (ValueError, IndexError) as e:
                # Log but continue processing other matches
                self._total_failed += 1
                continue
        
        # If we expected data but got nothing, count as failed
        if not readings and raw_data.strip():
            # Only count as failed if it looks like it should be data
            if ';' in raw_data:
                self._total_failed += 1
        
        return readings
    
    def parse_single(self, raw_data: str, timestamp: Optional[datetime] = None) -> Optional[ParsedReading]:
        """
        Parse raw data expecting exactly one reading.
        
        Args:
            raw_data: The raw data string from the device
            timestamp: Optional timestamp (defaults to now)
        
        Returns:
            ParsedReading if successful, None if no valid reading found
        """
        readings = self.parse(raw_data, timestamp)
        return readings[0] if readings else None
    
    def validate_format(self, raw_data: str) -> bool:
        """
        Check if the raw data matches the expected format.
        
        Args:
            raw_data: The raw data string to validate
        
        Returns:
            True if the data matches the expected format, False otherwise
        """
        if self.READING_PATTERN.search(raw_data):
            return True
        if not self._strict_mode and self.ALT_PATTERN.search(raw_data):
            return True
        return False
    
    def reset_stats(self) -> None:
        """Reset the parsing statistics."""
        self._total_parsed = 0
        self._total_failed = 0
    
    # endregion
    
    # region Static Methods
    
    @staticmethod
    def extract_animal_ids(raw_data: str) -> List[str]:
        """
        Extract all animal IDs from raw data without full parsing.
        
        Useful for quick filtering or routing decisions.
        
        Args:
            raw_data: The raw data string
        
        Returns:
            List of animal ID strings found in the data
        """
        # Simple pattern to extract hex IDs
        pattern = re.compile(r';[A-Fa-f0-9]{4,},', re.IGNORECASE)
        matches = pattern.findall(raw_data)
        
        # Clean up the matches (remove semicolons and commas)
        return [m.strip(';,').upper() for m in matches]
    
    @staticmethod
    def format_reading(packet_number: int, zone: int, animal_id: str, temperature: float) -> str:
        """
        Format values into the standard data format string.
        
        Useful for testing or generating mock data.
        
        Args:
            packet_number: The packet sequence number
            zone: The zone number
            animal_id: The animal identifier
            temperature: The temperature value
        
        Returns:
            Formatted string in device data format
        """
        return f";{packet_number};{zone};{animal_id},{temperature};"
    
    # endregion
    
    def __repr__(self) -> str:
        return f"DataParser(parsed={self._total_parsed}, failed={self._total_failed}, rate={self.success_rate:.1%})"

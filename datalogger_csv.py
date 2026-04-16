"""
Data Logger CSV module - Handles writing data to CSV files.

Provides thread-safe CSV logging with automatic file rotation,
header management, and buffered writing for performance.
"""

import csv
import os
import threading
from datetime import datetime
from typing import Optional, List, Any, Dict
from dataclasses import dataclass
from pathlib import Path
from enum import Enum, auto


class WriteMode(Enum):
    """Specifies how to handle existing files."""
    APPEND = auto()      # Append to existing file
    OVERWRITE = auto()   # Overwrite existing file
    TIMESTAMP = auto()   # Create new file with timestamp


@dataclass
class CSVLoggerConfig:
    """
    Configuration for a CSV logger.
    
    Attributes:
        base_filename: Base name for the CSV file (without extension)
        output_directory: Directory where CSV files will be written
        write_mode: How to handle existing files
        include_header: Whether to write a header row
        flush_immediately: Whether to flush after each write
        date_format: Format string for timestamp columns
    """
    base_filename: str
    output_directory: str = "."
    write_mode: WriteMode = WriteMode.APPEND
    include_header: bool = True
    flush_immediately: bool = True
    date_format: str = "%Y-%m-%d %H:%M:%S.%f"


class DataLoggerCSV:
    """
    Thread-safe CSV data logger for continuous data collection.
    
    Supports multiple concurrent writers and handles file rotation
    for long-running 24/7 operations.
    
    Example:
        >>> config = CSVLoggerConfig(base_filename="reader_1", output_directory="./data")
        >>> logger = DataLoggerCSV(config, headers=["timestamp", "packet", "zone", "animal_id", "temperature"])
        >>> logger.open()
        >>> logger.write_row(["2024-01-01 12:00:00", 2474, 8, "ABCD1234", 28.9])
        >>> logger.close()
    """
    
    def __init__(self, config: CSVLoggerConfig, headers: Optional[List[str]] = None):
        """
        Initialize a new DataLoggerCSV instance.
        
        Args:
            config: Logger configuration settings
            headers: Optional list of column headers
        """
        self._config = config
        self._headers = headers or []
        self._file = None
        self._writer: Optional[csv.writer] = None
        self._lock = threading.Lock()
        self._is_open = False
        self._rows_written = 0
        self._file_path: Optional[Path] = None
        self._created_time: Optional[datetime] = None
    
    # region Properties
    
    @property
    def is_open(self) -> bool:
        """Returns True if the logger is currently open for writing."""
        return self._is_open
    
    @property
    def file_path(self) -> Optional[Path]:
        """Gets the current file path being written to."""
        return self._file_path
    
    @property
    def rows_written(self) -> int:
        """Gets the number of rows written to the current file."""
        return self._rows_written
    
    @property
    def config(self) -> CSVLoggerConfig:
        """Gets the logger configuration."""
        return self._config
    
    # endregion
    
    # region Public Methods
    
    def open(self) -> bool:
        """
        Open the CSV file for writing.
        
        Returns:
            True if the file was opened successfully, False otherwise.
        """
        with self._lock:
            if self._is_open:
                return True
            
            try:
                # Ensure output directory exists
                output_dir = Path(self._config.output_directory)
                output_dir.mkdir(parents=True, exist_ok=True)
                
                # Generate filename based on write mode
                self._file_path = self._generate_file_path()
                
                # Determine if we need to write headers
                file_exists = self._file_path.exists()
                write_header = (
                    self._config.include_header and 
                    self._headers and
                    (self._config.write_mode != WriteMode.APPEND or not file_exists)
                )
                
                # Open file in appropriate mode
                mode = 'w' if self._config.write_mode == WriteMode.OVERWRITE else 'a'
                self._file = open(self._file_path, mode, newline='', encoding='utf-8')
                self._writer = csv.writer(self._file)
                
                # Write header if needed
                if write_header:
                    self._writer.writerow(self._headers)
                    if self._config.flush_immediately:
                        self._file.flush()
                
                self._is_open = True
                self._created_time = datetime.now()
                self._rows_written = 0
                
                return True
                
            except IOError as e:
                print(f"Error opening CSV file: {e}")
                return False
    
    def close(self) -> None:
        """Close the CSV file."""
        with self._lock:
            if self._file:
                try:
                    self._file.flush()
                    self._file.close()
                except Exception:
                    pass
                self._file = None
                self._writer = None
            self._is_open = False
    
    def write_row(self, row: List[Any]) -> bool:
        """
        Write a single row to the CSV file.
        
        Args:
            row: List of values to write as a row
        
        Returns:
            True if the row was written successfully, False otherwise.
        """
        with self._lock:
            if not self._is_open or self._writer is None:
                return False
            
            try:
                self._writer.writerow(row)
                self._rows_written += 1
                
                if self._config.flush_immediately and self._file:
                    self._file.flush()
                
                return True
                
            except IOError as e:
                print(f"Error writing to CSV: {e}")
                return False
    
    def write_rows(self, rows: List[List[Any]]) -> int:
        """
        Write multiple rows to the CSV file.
        
        Args:
            rows: List of rows to write
        
        Returns:
            Number of rows successfully written.
        """
        with self._lock:
            if not self._is_open or self._writer is None:
                return 0
            
            try:
                written = 0
                for row in rows:
                    self._writer.writerow(row)
                    written += 1
                
                self._rows_written += written
                
                if self._config.flush_immediately and self._file:
                    self._file.flush()
                
                return written
                
            except IOError as e:
                print(f"Error writing to CSV: {e}")
                return 0
    
    def write_data_record(self, timestamp: datetime, packet_number: int, 
                          zone: int, animal_id: str, temperature: float,
                          raw_data: Optional[str] = None) -> bool:
        """
        Write a parsed data record to the CSV file.
        
        This is a convenience method for the specific data format used
        by the temperature monitoring system.
        
        Args:
            timestamp: When the reading was received
            packet_number: The packet sequence number
            zone: The zone number
            animal_id: The animal identifier
            temperature: The temperature reading
            raw_data: Optional raw data string for debugging
        
        Returns:
            True if the record was written successfully, False otherwise.
        """
        formatted_timestamp = timestamp.strftime(self._config.date_format)
        
        row = [formatted_timestamp, packet_number, zone, animal_id, temperature]
        if raw_data is not None:
            row.append(raw_data)
        
        return self.write_row(row)
    
    def rotate_file(self) -> bool:
        """
        Rotate to a new file (close current and open new with timestamp).
        
        Returns:
            True if rotation was successful, False otherwise.
        """
        self.close()
        
        # Force timestamp mode for the new file
        original_mode = self._config.write_mode
        self._config.write_mode = WriteMode.TIMESTAMP
        
        result = self.open()
        
        # Restore original mode
        self._config.write_mode = original_mode
        
        return result
    
    # endregion
    
    # region Private Methods
    
    def _generate_file_path(self) -> Path:
        """
        Generate the full file path based on configuration.
        
        Returns:
            Path object for the CSV file.
        """
        output_dir = Path(self._config.output_directory)
        base_name = self._config.base_filename
        
        if self._config.write_mode == WriteMode.TIMESTAMP:
            # Include timestamp in filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{base_name}_{timestamp}.csv"
        else:
            filename = f"{base_name}.csv"
        
        return output_dir / filename
    
    # endregion
    
    # region Context Manager Support
    
    def __enter__(self) -> 'DataLoggerCSV':
        """Support for 'with' statement - open on enter."""
        self.open()
        return self
    
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Support for 'with' statement - close on exit."""
        self.close()
    
    # endregion
    
    def __repr__(self) -> str:
        return f"DataLoggerCSV(file='{self._file_path}', rows={self._rows_written}, open={self._is_open})"


class MultiDeviceLogger:
    """
    Manages CSV loggers for multiple devices simultaneously.
    
    Creates and manages separate CSV files for each device,
    useful for logging data from up to 24 devices concurrently.
    
    Example:
        >>> logger = MultiDeviceLogger(output_directory="./data")
        >>> logger.log_reading("Device-1", datetime.now(), 2474, 8, "ABCD1234", 28.9)
        >>> logger.close_all()
    """
    
    DEFAULT_HEADERS = ["DateTime", "UID", "Temperature", "Zone"]
    
    def __init__(self, output_directory: str = "./data", headers: Optional[List[str]] = None):
        """
        Initialize a new MultiDeviceLogger instance.
        
        Args:
            output_directory: Directory where all CSV files will be written
            headers: Optional list of column headers (uses default if not provided)
        """
        self._output_directory = output_directory
        self._headers = headers or self.DEFAULT_HEADERS
        self._loggers: Dict[str, DataLoggerCSV] = {}
        self._lock = threading.Lock()
    
    def get_or_create_logger(self, device_name: str) -> DataLoggerCSV:
        """
        Get an existing logger or create a new one for a device.
        
        Args:
            device_name: The name of the device
        
        Returns:
            DataLoggerCSV instance for the device.
        """
        with self._lock:
            if device_name not in self._loggers:
                # Create a safe filename from device name
                safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in device_name)
                
                config = CSVLoggerConfig(
                    base_filename=safe_name,
                    output_directory=self._output_directory,
                    write_mode=WriteMode.APPEND,
                    include_header=True,
                    flush_immediately=True
                )
                
                logger = DataLoggerCSV(config, headers=self._headers)
                logger.open()
                self._loggers[device_name] = logger
            
            return self._loggers[device_name]
    
    def log_reading(self, device_name: str, timestamp: datetime,
                    zone: int, animal_id: str, temperature: float) -> bool:
        """
        Log a reading for a specific device.
        
        Args:
            device_name: The name of the device
            timestamp: When the reading was received
            zone: The zone number
            animal_id: The animal identifier (UID)
            temperature: The temperature reading
        
        Returns:
            True if logged successfully, False otherwise.
        """
        logger = self.get_or_create_logger(device_name)
        formatted_timestamp = timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # Trim to milliseconds
        row = [formatted_timestamp, animal_id, temperature, zone]
        return logger.write_row(row)
    
    def close_all(self) -> None:
        """Close all open loggers."""
        with self._lock:
            for logger in self._loggers.values():
                logger.close()
            self._loggers.clear()
    
    @property
    def active_loggers(self) -> int:
        """Gets the number of active loggers."""
        return len(self._loggers)
    
    def __enter__(self) -> 'MultiDeviceLogger':
        return self
    
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close_all()

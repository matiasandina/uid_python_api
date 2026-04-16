"""
Animal Registry module - Centralized management of all tracked animals.

Provides thread-safe access to animal objects and periodic display
of live animal status to the console.
"""

import threading
import time
from datetime import datetime
from typing import Dict, Optional, List, Callable
from animal import Animal


class AnimalRegistry:
    """
    Thread-safe registry for managing all tracked animals.
    
    Automatically creates Animal objects when new IDs are encountered
    and provides methods for querying and displaying animal status.
    
    Example:
        >>> registry = AnimalRegistry()
        >>> registry.record_reading("ABCD1234", 28.5, zone=8, packet_number=2474)
        >>> registry.start_display_loop(interval_seconds=5)
        >>> # ... later ...
        >>> registry.stop_display_loop()
    """
    
    def __init__(self, averaging_window_seconds: int = 60):
        """
        Initialize a new AnimalRegistry instance.
        
        Args:
            averaging_window_seconds: Window size for temperature averaging
        """
        self._animals: Dict[str, Animal] = {}
        self._lock = threading.RLock()  # Reentrant lock for nested calls
        self._averaging_window_seconds = averaging_window_seconds
        self._display_thread: Optional[threading.Thread] = None
        self._stop_display_event = threading.Event()
        self._total_readings = 0
        self._created_time = datetime.now()
        
        # Callback for custom handling of new animals
        self.on_new_animal: Optional[Callable[[Animal], None]] = None
        self.on_reading_recorded: Optional[Callable[[Animal, float], None]] = None
    
    # region Properties
    
    @property
    def animal_count(self) -> int:
        """Gets the number of unique animals tracked."""
        with self._lock:
            return len(self._animals)
    
    @property
    def total_readings(self) -> int:
        """Gets the total number of readings recorded across all animals."""
        return self._total_readings
    
    @property
    def animal_ids(self) -> List[str]:
        """Gets a list of all tracked animal IDs."""
        with self._lock:
            return list(self._animals.keys())
    
    @property
    def is_display_running(self) -> bool:
        """Returns True if the display loop is running."""
        return self._display_thread is not None and self._display_thread.is_alive()
    
    # endregion
    
    # region Public Methods
    
    def record_reading(
        self,
        animal_id: str,
        temperature: float,
        zone: int,
        packet_number: int,
        timestamp: Optional[datetime] = None,
        device_name: Optional[str] = None,
    ) -> Animal:
        """
        Record a temperature reading for an animal.
        
        Creates a new Animal object if this ID hasn't been seen before.
        
        Args:
            animal_id: The unique identifier for the animal
            temperature: The temperature reading
            zone: The zone number where the reading was taken
            packet_number: The packet sequence number
            timestamp: Optional timestamp (defaults to now)
        
        Returns:
            The Animal object that received the reading
        """
        with self._lock:
            # Get or create animal
            is_new = animal_id not in self._animals
            animal = self._get_or_create_animal(animal_id)
            
            # Record the reading
            animal.add_reading(
                temperature=temperature,
                zone=zone,
                packet_number=packet_number,
                timestamp=timestamp,
                device_name=device_name,
            )
            
            self._total_readings += 1
            
            # Fire callbacks
            if is_new and self.on_new_animal:
                self.on_new_animal(animal)
            
            if self.on_reading_recorded:
                self.on_reading_recorded(animal, temperature)
            
            return animal
    
    def get_animal(self, animal_id: str) -> Optional[Animal]:
        """
        Get an animal by ID.
        
        Args:
            animal_id: The unique identifier for the animal
        
        Returns:
            The Animal object, or None if not found
        """
        with self._lock:
            return self._animals.get(animal_id)
    
    def get_all_animals(self) -> List[Animal]:
        """
        Get all tracked animals.
        
        Returns:
            List of all Animal objects
        """
        with self._lock:
            return list(self._animals.values())
    
    def get_animals_in_zone(self, zone: int) -> List[Animal]:
        """
        Get all animals last seen in a specific zone.
        
        Args:
            zone: The zone number to filter by
        
        Returns:
            List of Animal objects last seen in the specified zone
        """
        with self._lock:
            return [
                animal for animal in self._animals.values()
                if animal.last_zone == zone
            ]
    
    def get_animals_by_temperature_range(self, min_temp: float, max_temp: float) -> List[Animal]:
        """
        Get animals with average temperatures in a specified range.
        
        Args:
            min_temp: Minimum average temperature (inclusive)
            max_temp: Maximum average temperature (inclusive)
        
        Returns:
            List of Animal objects within the temperature range
        """
        with self._lock:
            result = []
            for animal in self._animals.values():
                avg = animal.average_temperature
                if avg is not None and min_temp <= avg <= max_temp:
                    result.append(animal)
            return result
    
    def get_stale_animals(self, seconds: float = 300) -> List[Animal]:
        """
        Get animals that haven't been scanned recently.
        
        Args:
            seconds: Time threshold in seconds (default 5 minutes)
        
        Returns:
            List of Animal objects not scanned within the threshold
        """
        with self._lock:
            result = []
            for animal in self._animals.values():
                time_since = animal.seconds_since_last_scan
                if time_since is not None and time_since > seconds:
                    result.append(animal)
            return result
    
    def start_display_loop(self, interval_seconds: float = 10.0) -> None:
        """
        Start a background thread that periodically displays animal status.
        
        Args:
            interval_seconds: How often to display status (default 10 seconds)
        """
        if self.is_display_running:
            return
        
        self._stop_display_event.clear()
        self._display_thread = threading.Thread(
            target=self._display_loop,
            args=(interval_seconds,),
            daemon=True,
            name="AnimalRegistry-Display"
        )
        self._display_thread.start()
    
    def stop_display_loop(self) -> None:
        """Stop the background display thread."""
        self._stop_display_event.set()
        if self._display_thread and self._display_thread.is_alive():
            self._display_thread.join(timeout=2.0)
    
    def display_all_animals(self) -> None:
        """Display the current status of all animals to the console."""
        with self._lock:
            animals = list(self._animals.values())
        
        if not animals:
            print("\n[Animal Registry] No animals tracked yet.\n")
            return
        
        # Sort by animal ID for consistent display
        animals.sort(key=lambda a: a.animal_id)
        
        print("\n" + "=" * 80)
        print(f"[Animal Registry] {len(animals)} animals tracked | "
              f"Total readings: {self._total_readings} | "
              f"Time: {datetime.now().strftime('%H:%M:%S')}")
        print("-" * 80)
        
        for animal in animals:
            print(f"  {animal.to_display_string()}")
        
        print("=" * 80 + "\n")
    
    def get_summary(self) -> Dict:
        """
        Get a summary dictionary of registry statistics.
        
        Returns:
            Dictionary containing registry statistics
        """
        with self._lock:
            animals = list(self._animals.values())
        
        temps = [a.average_temperature for a in animals if a.average_temperature is not None]
        
        return {
            "animal_count": len(animals),
            "total_readings": self._total_readings,
            "avg_temperature": sum(temps) / len(temps) if temps else None,
            "min_temperature": min(temps) if temps else None,
            "max_temperature": max(temps) if temps else None,
            "uptime_seconds": (datetime.now() - self._created_time).total_seconds()
        }
    
    def clear(self) -> None:
        """Remove all animals from the registry."""
        with self._lock:
            self._animals.clear()
            self._total_readings = 0
    
    # endregion
    
    # region Private Methods
    
    def _get_or_create_animal(self, animal_id: str) -> Animal:
        """
        Get an existing animal or create a new one.
        
        Args:
            animal_id: The unique identifier for the animal
        
        Returns:
            The Animal object
        """
        if animal_id not in self._animals:
            self._animals[animal_id] = Animal(
                animal_id=animal_id,
                averaging_window_seconds=self._averaging_window_seconds
            )
        return self._animals[animal_id]
    
    def _display_loop(self, interval_seconds: float) -> None:
        """
        Background loop for periodic status display.
        
        Args:
            interval_seconds: How often to display status
        """
        while not self._stop_display_event.is_set():
            self.display_all_animals()
            self._stop_display_event.wait(interval_seconds)
    
    # endregion
    
    # region Magic Methods
    
    def __len__(self) -> int:
        """Returns the number of animals tracked."""
        return self.animal_count
    
    def __contains__(self, animal_id: str) -> bool:
        """Check if an animal ID is in the registry."""
        with self._lock:
            return animal_id in self._animals
    
    def __getitem__(self, animal_id: str) -> Animal:
        """Get an animal by ID using indexer syntax."""
        animal = self.get_animal(animal_id)
        if animal is None:
            raise KeyError(f"Animal '{animal_id}' not found in registry")
        return animal
    
    def __iter__(self):
        """Iterate over all animals in the registry."""
        with self._lock:
            return iter(list(self._animals.values()))
    
    def __repr__(self) -> str:
        return f"AnimalRegistry(animals={self.animal_count}, readings={self._total_readings})"
    
    # endregion

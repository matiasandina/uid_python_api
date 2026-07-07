from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from trigger_scheduler import TriggerScheduler


@dataclass
class FakeReading:
    timestamp: datetime
    temperature: float = 32.0
    zone: int = 1
    packet_number: int = 1
    device_name: str = "Reader-1"


class FakeAnimal:
    def __init__(self, animal_id: str) -> None:
        self.animal_id = animal_id
        self.last_device_name = "Reader-1"
        self.seconds_since_last_scan: Optional[float] = 0.0
        self.readings = [FakeReading(datetime.now())]

    def get_readings_in_window(self):
        return list(self.readings)


class FakeRegistry:
    def __init__(self, animal: FakeAnimal) -> None:
        self.animal = animal

    def get_all_animals(self):
        return [self.animal]


def true_classifier(animal_id, window_readings, now, config):
    return {
        "trigger": True,
        "condition_true": True,
        "action": "pulse",
        "stimulus_id": "below_33_c_30s",
        "reason": "window ready; mean below threshold",
        "meta": {"count": len(window_readings), "animal_id": animal_id},
    }


class TriggerSchedulerMissingDataTests(unittest.TestCase):
    def make_scheduler(
        self,
        animal: FakeAnimal,
        events: list,
        missing_events: list,
        missing_animal_stop_clf_seconds: Optional[float] = None,
    ) -> TriggerScheduler:
        return TriggerScheduler(
            registry=FakeRegistry(animal),
            interval_seconds=1.0,
            window_seconds=30.0,
            missing_animal_seconds=120.0,
            classifier=true_classifier,
            trigger_mode="window",
            classifier_config={"stimulus_id": "below_33_c_30s"},
            missing_animal_stop_clf_seconds=missing_animal_stop_clf_seconds,
            rule_id="box1",
            device_names=["Reader-1"],
            target_channels=["ch1"],
            assigned_animal_ids=[animal.animal_id],
            on_trigger=events.append,
            on_missing=lambda rule_id, animal_id, seconds_since: missing_events.append(
                (rule_id, animal_id, seconds_since)
            ),
            quiet_mode=True,
        )

    def test_no_readings_forces_stop_when_window_was_active(self):
        animal = FakeAnimal("ABC123")
        events = []
        missing_events = []
        scheduler = self.make_scheduler(animal, events, missing_events)

        scheduler._evaluate_once()
        animal.readings = []
        scheduler._evaluate_once()

        self.assertEqual([event.action for event in events], ["start", "stop"])
        self.assertEqual(events[-1].reason, "no readings available")

    def test_missing_animal_forces_stop_when_window_was_active(self):
        animal = FakeAnimal("ABC123")
        events = []
        missing_events = []
        scheduler = self.make_scheduler(animal, events, missing_events)

        scheduler._evaluate_once()
        animal.seconds_since_last_scan = 121.0
        scheduler._evaluate_once()

        self.assertEqual([event.action for event in events], ["start", "stop"])
        self.assertEqual(events[-1].reason, "missing data; last seen 121s ago")
        self.assertEqual(missing_events, [("box1", "ABC123", 121.0)])

    def test_missing_animal_warning_can_continue_classifier_until_stop_threshold(self):
        animal = FakeAnimal("ABC123")
        events = []
        missing_events = []
        scheduler = self.make_scheduler(
            animal,
            events,
            missing_events,
            missing_animal_stop_clf_seconds=3600.0,
        )

        scheduler._evaluate_once()
        animal.seconds_since_last_scan = 121.0
        scheduler._evaluate_once()

        self.assertEqual([event.action for event in events], ["start"])
        self.assertEqual(missing_events, [("box1", "ABC123", 121.0)])

    def test_missing_animal_stop_threshold_forces_stop(self):
        animal = FakeAnimal("ABC123")
        events = []
        missing_events = []
        scheduler = self.make_scheduler(
            animal,
            events,
            missing_events,
            missing_animal_stop_clf_seconds=3600.0,
        )

        scheduler._evaluate_once()
        animal.seconds_since_last_scan = 3601.0
        scheduler._evaluate_once()

        self.assertEqual([event.action for event in events], ["start", "stop"])
        self.assertEqual(events[-1].reason, "missing data; last seen 3601s ago")
        self.assertEqual(missing_events, [("box1", "ABC123", 3601.0)])


if __name__ == "__main__":
    unittest.main()

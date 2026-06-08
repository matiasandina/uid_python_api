import threading
import time
import unittest
from types import SimpleNamespace

from stim_controller import StimulationController, StimulusConfig


class FakeLightSource:
    def __init__(self):
        self.events = []
        self._lock = threading.Lock()

    def connect(self):
        return

    def close(self):
        return

    def start_channel(self, channel_name):
        with self._lock:
            self.events.append(("start", channel_name, time.monotonic()))

    def stop_channel(self, channel_name):
        with self._lock:
            self.events.append(("stop", channel_name, time.monotonic()))

    def stop_all(self):
        with self._lock:
            self.events.append(("stop_all", "*", time.monotonic()))


def event(action):
    return SimpleNamespace(action=action, meta={"channels": ["ch1"]})


class StimulationControllerTests(unittest.TestCase):
    def test_window_start_repeats_train_until_stop(self):
        driver = FakeLightSource()
        controller = StimulationController(
            StimulusConfig(
                enabled=True,
                mode="laser",
                window_on_seconds=0.02,
                train_on_seconds=0.02,
                train_off_seconds=0.02,
                driver=driver,
            )
        )

        controller.handle_trigger_event(event("start"))
        time.sleep(0.07)
        controller.handle_trigger_event(event("stop"))
        events_at_stop = list(driver.events)
        time.sleep(0.05)

        starts = [item for item in events_at_stop if item[0] == "start"]
        stops = [item for item in events_at_stop if item[0] == "stop"]
        self.assertGreaterEqual(len(starts), 2)
        self.assertGreaterEqual(len(stops), 2)
        self.assertEqual(driver.events, events_at_stop)
        self.assertEqual(controller.status()["state"], StimulationController.STATE_READY)

    def test_window_start_without_train_off_runs_until_stop(self):
        driver = FakeLightSource()
        controller = StimulationController(
            StimulusConfig(
                enabled=True,
                mode="laser",
                window_on_seconds=0.02,
                train_on_seconds=0.02,
                train_off_seconds=0.0,
                driver=driver,
            )
        )

        controller.handle_trigger_event(event("start"))
        time.sleep(0.05)
        controller.handle_trigger_event(event("stop"))

        starts = [item for item in driver.events if item[0] == "start"]
        stops = [item for item in driver.events if item[0] == "stop"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(stops), 1)


if __name__ == "__main__":
    unittest.main()

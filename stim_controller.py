from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

from doric_light_source import ChannelHardwareConfig, DoricLightSource, LightSourceSquareConfig
from live_state import LiveState


class NoopLightSource:
    def connect(self) -> None:
        return

    def close(self) -> None:
        return

    def start_channel(self, channel_name: str) -> None:
        return

    def stop_channel(self, channel_name: str) -> None:
        return

    def start_all(self) -> None:
        return

    def stop_all(self) -> None:
        return


@dataclass(frozen=True)
class StimulusConfig:
    enabled: bool
    mode: str
    window_on_seconds: float
    train_on_seconds: float
    train_off_seconds: float
    driver: DoricLightSource | NoopLightSource


class StimulationController:
    STATE_DISABLED = "disabled"
    STATE_READY = "ready"
    STATE_ACTIVE = "active"
    STATE_FAULT = "fault"

    def __init__(self, config: StimulusConfig, quiet_mode: bool = True) -> None:
        self._config = config
        self._quiet_mode = quiet_mode
        self._lock = threading.Lock()
        self._timers: Dict[str, threading.Timer] = {}
        self._next_allowed_at: Dict[str, float] = {}
        self._active_window_channels: set[str] = set()
        self._state = self.STATE_DISABLED if not config.enabled else self.STATE_READY
        self._fault_reason: Optional[str] = None

    @classmethod
    def from_config(
        cls,
        stimulus_cfg: Dict[str, Any],
        *,
        quiet_mode: bool = True,
        live_state: Optional[LiveState] = None,
    ) -> "StimulationController":
        square_cfg = stimulus_cfg["square"]
        square = LightSourceSquareConfig(
            period_ms=float(square_cfg["period_ms"]),
            time_on_ms=float(square_cfg["time_on_ms"]),
            nb_of_seq=int(square_cfg["nb_of_seq"]),
            nb_of_pulses_per_seq=int(square_cfg["nb_of_pulses_per_seq"]),
            starting_delay_ms=int(square_cfg["starting_delay_ms"]),
            delay_between_seq_ms=int(square_cfg["delay_between_seq_ms"]),
            ttl_output=bool(square_cfg["ttl_output"]),
        )

        mode = str(stimulus_cfg.get("mode", "monitor")).strip().lower()
        if mode != "laser":
            driver = NoopLightSource()
        else:
            preflight_driver = live_state.laser_driver if live_state is not None else None
            if isinstance(preflight_driver, DoricLightSource):
                driver = preflight_driver
            else:
                driver = DoricLightSource(
                    dll_path=str(stimulus_cfg["dll_path"]),
                    port=int(stimulus_cfg["port"]) if stimulus_cfg.get("port") is not None else None,
                    uid=stimulus_cfg.get("uid"),
                    channels={
                        str(k): ChannelHardwareConfig(index=int(v["index"]), current_ma=int(v["current_ma"]))
                        for k, v in stimulus_cfg["channels"].items()
                    },
                    square=square,
                )

        config = StimulusConfig(
            enabled=bool(stimulus_cfg.get("enabled", False)),
            mode=mode,
            window_on_seconds=float(stimulus_cfg["window_on_seconds"]),
            train_on_seconds=float(stimulus_cfg.get("train", {}).get("on_seconds", stimulus_cfg["window_on_seconds"])),
            train_off_seconds=float(stimulus_cfg.get("train", {}).get("off_seconds", 0.0)),
            driver=driver,
        )
        return cls(config, quiet_mode=quiet_mode)

    def start(self) -> bool:
        if not self._config.enabled:
            return True
        try:
            self._config.driver.connect()
            with self._lock:
                self._state = self.STATE_READY
                self._fault_reason = None
            return True
        except Exception as exc:
            self._enter_fault("stim init failed", exc)
            return False

    def stop(self) -> None:
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._active_window_channels.clear()
        try:
            self._config.driver.stop_all()
        except Exception as exc:
            self._emit(f"[Stimulus] stop_all failed during shutdown: {exc}")
        try:
            self._config.driver.close()
        except Exception as exc:
            self._emit(f"[Stimulus] close failed during shutdown: {exc}")

    def describe(self) -> str:
        return (
            f"stimulus enabled={self._config.enabled} mode={self._config.mode} "
            f"window_on={self._config.window_on_seconds}s "
            f"train_on={self._config.train_on_seconds}s train_off={self._config.train_off_seconds}s "
            "channels=event.target_channels"
        )

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._config.enabled,
                "mode": self._config.mode,
                "state": self._state,
                "fault_reason": self._fault_reason,
            }

    def handle_trigger_event(self, event: Any) -> None:
        if not self._config.enabled:
            return
        if self._is_faulted():
            self._emit("[Stimulus] Ignored trigger (controller in FAULT).")
            return
        if self._config.mode != "laser":
            self._emit("[Stimulus] Monitor mode: trigger observed, no hardware output sent.")
            return

        action = str(getattr(event, "action", "pulse")).lower()
        channels = self._resolve_channels(event=event)
        if not channels:
            self._emit("[Stimulus] No target channels supplied in event metadata.")
            return

        try:
            if action == "start":
                for channel in channels:
                    self._start_window_channel(channel)
            elif action == "stop":
                for channel in channels:
                    self._stop_window_channel(channel)
            else:
                for channel in channels:
                    self._pulse_or_train_channel(channel)
            with self._lock:
                if action == "stop" and not self._active_window_channels and not self._timers:
                    self._state = self.STATE_READY
                else:
                    self._state = self.STATE_ACTIVE
        except Exception as exc:
            self._enter_fault("stim trigger action failed", exc)

    def _pulse_or_train_channel(self, channel_name: str) -> None:
        duration = self._config.train_on_seconds or self._config.window_on_seconds
        cooldown = self._config.train_off_seconds
        with self._lock:
            next_allowed = self._next_allowed_at.get(channel_name, 0.0)
            tnow = time.monotonic()
            if tnow < next_allowed:
                return
            self._config.driver.start_channel(channel_name)
            existing = self._timers.pop(channel_name, None)
            if existing:
                existing.cancel()
            timer = threading.Timer(duration, self._config.driver.stop_channel, args=(channel_name,))
            timer.daemon = True
            self._timers[channel_name] = timer
            timer.start()
            self._next_allowed_at[channel_name] = tnow + duration + max(0.0, cooldown)

    def _start_window_channel(self, channel_name: str) -> None:
        with self._lock:
            if channel_name in self._active_window_channels:
                return
            self._active_window_channels.add(channel_name)
        self._start_window_train_on(channel_name)

    def _stop_window_channel(self, channel_name: str) -> None:
        with self._lock:
            self._active_window_channels.discard(channel_name)
            existing = self._timers.pop(channel_name, None)
            if existing:
                existing.cancel()
        self._config.driver.stop_channel(channel_name)

    def _start_window_train_on(self, channel_name: str) -> None:
        duration = self._config.train_on_seconds or self._config.window_on_seconds
        cooldown = self._config.train_off_seconds
        with self._lock:
            if channel_name not in self._active_window_channels:
                return
            self._config.driver.start_channel(channel_name)
            existing = self._timers.pop(channel_name, None)
            if existing:
                existing.cancel()
            if cooldown <= 0:
                return
            timer = threading.Timer(duration, self._finish_window_train_on, args=(channel_name,))
            timer.daemon = True
            self._timers[channel_name] = timer
            timer.start()

    def _finish_window_train_on(self, channel_name: str) -> None:
        cooldown = self._config.train_off_seconds
        with self._lock:
            self._timers.pop(channel_name, None)
            if channel_name not in self._active_window_channels:
                return
            self._config.driver.stop_channel(channel_name)
            if cooldown <= 0:
                return
            timer = threading.Timer(cooldown, self._start_window_train_on, args=(channel_name,))
            timer.daemon = True
            self._timers[channel_name] = timer
            timer.start()

    def _resolve_channels(self, event: Any) -> List[str]:
        meta = getattr(event, "meta", {})
        if isinstance(meta, dict):
            direct_channels = meta.get("channels")
            if isinstance(direct_channels, list):
                parsed = [str(ch) for ch in direct_channels if str(ch).strip()]
                if parsed:
                    return parsed
        return []

    def _is_faulted(self) -> bool:
        with self._lock:
            return self._state == self.STATE_FAULT

    def _enter_fault(self, reason: str, exc: Exception) -> None:
        message = f"{reason}: {exc}"
        with self._lock:
            self._state = self.STATE_FAULT
            self._fault_reason = message
        self._emit(f"[Stimulus] FAULT: {message}. Forcing stop.")
        try:
            self._config.driver.stop_all()
        except Exception:
            pass
        try:
            self._config.driver.close()
        except Exception:
            pass

    def _emit(self, message: str) -> None:
        if not self._quiet_mode:
            print(message)

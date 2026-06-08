"""
Trigger scheduler module - Evaluates animal windows on a fixed cadence.

Runs a classifier per animal and emits trigger events. Also flags missing animals.
"""

from __future__ import annotations

import importlib
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional


ClassifierFn = Callable[[str, List[Dict[str, Any]], datetime, Dict[str, Any]], Optional[Dict[str, Any]]]


@dataclass
class TriggerEvent:
    """Represents a trigger decision from a classifier."""
    animal_event_id: Optional[int]
    animal_id: str
    rule_id: Optional[str]
    action: str
    stimulus_id: str
    reason: str
    meta: Dict[str, Any]
    timestamp: datetime


@dataclass
class TriggerAnimalStatus:
    """Live trigger/evaluation status per animal for UI and diagnostics."""
    rule_id: str
    animal_id: str
    device_names: List[str]
    target_channels: List[str]
    condition_true: bool
    current_avg_temp: Optional[float]
    sample_count: int
    observed_duration_seconds: Optional[float]
    required_duration_seconds: Optional[float]
    coverage_tolerance_seconds: Optional[float]
    threshold_c: Optional[float]
    direction: str
    aggregation: str
    threshold_met: Optional[bool]
    coverage_ready: Optional[bool]
    min_samples: Optional[int]
    last_action: str
    stimulus_id: str
    reason: str
    last_eval_time: datetime
    last_event_time: Optional[datetime]
    trigger_count: int


def load_classifier(spec: str) -> ClassifierFn:
    """Load a classifier from 'module:function' or 'module' (defaults to evaluate)."""
    if ":" in spec:
        module_name, func_name = spec.split(":", 1)
    else:
        module_name, func_name = spec, "evaluate"
    module = importlib.import_module(module_name)
    func = getattr(module, func_name, None)
    if not callable(func):
        raise ValueError(f"Classifier '{spec}' not found or not callable")
    return func


class TriggerScheduler:
    """Periodic trigger evaluator for animal windows."""

    def __init__(
        self,
        registry,
        interval_seconds: float,
        window_seconds: float,
        missing_animal_seconds: float,
        classifier: ClassifierFn,
        trigger_mode: str = "pulse",
        classifier_config: Optional[Dict[str, Any]] = None,
        rule_id: str = "",
        device_names: Optional[List[str]] = None,
        target_channels: Optional[List[str]] = None,
        assigned_animal_ids: Optional[List[str]] = None,
        on_trigger: Optional[Callable[[TriggerEvent], None]] = None,
        on_missing: Optional[Callable[[str, str, float], None]] = None,
        quiet_mode: bool = True,
    ) -> None:
        self._registry = registry
        self._interval_seconds = interval_seconds
        self._window_seconds = window_seconds
        self._missing_animal_seconds = missing_animal_seconds
        self._classifier = classifier
        mode = str(trigger_mode).strip().lower()
        self._trigger_mode = mode if mode in ("pulse", "window") else "pulse"
        self._classifier_config = classifier_config or {}
        self._rule_id = str(rule_id).strip()
        self._device_names = {str(name).strip() for name in (device_names or []) if str(name).strip()}
        self._target_channels = [str(ch).strip() for ch in (target_channels or []) if str(ch).strip()]
        self._assigned_animal_ids = {
            str(animal_id).strip().upper() for animal_id in (assigned_animal_ids or []) if str(animal_id).strip()
        }
        self._on_trigger = on_trigger
        self._on_missing = on_missing
        self._quiet_mode = quiet_mode
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_missing_emitted: Dict[str, datetime] = {}
        self._window_state: Dict[str, bool] = {}
        self._status_lock = threading.Lock()
        self._status_by_animal: Dict[str, TriggerAnimalStatus] = {}

    @property
    def trigger_mode(self) -> str:
        return self._trigger_mode

    def get_status_snapshot(self) -> Dict[str, TriggerAnimalStatus]:
        with self._status_lock:
            return {k: v for k, v in self._status_by_animal.items()}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="TriggerScheduler",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            start = time.time()
            try:
                self._evaluate_once()
            except Exception as exc:
                if not self._quiet_mode:
                    print(f"[TriggerScheduler] Error: {exc}")
            elapsed = time.time() - start
            wait_time = max(0.0, self._interval_seconds - elapsed)
            self._stop_event.wait(wait_time)

    def _evaluate_once(self) -> None:
        now = datetime.now()
        animals = self._registry.get_all_animals()
        cutoff = now - timedelta(seconds=self._window_seconds)

        for animal in animals:
            if self._assigned_animal_ids and animal.animal_id.upper() not in self._assigned_animal_ids:
                continue
            # Missing animal detection
            seconds_since = animal.seconds_since_last_scan
            if (
                seconds_since is not None
                and self._missing_animal_seconds > 0
                and seconds_since > self._missing_animal_seconds
                and (not self._device_names or animal.last_device_name in self._device_names)
            ):
                last_emit = self._last_missing_emitted.get(animal.animal_id)
                if last_emit is None or (now - last_emit).total_seconds() >= self._interval_seconds:
                    self._last_missing_emitted[animal.animal_id] = now
                    if self._on_missing:
                        self._on_missing(self._rule_id, animal.animal_id, seconds_since)
                self._handle_no_evidence(
                    animal_id=animal.animal_id,
                    now=now,
                    reason=f"missing data; last seen {seconds_since:.0f}s ago",
                    meta={
                        "seconds_since_last_scan": seconds_since,
                        "missing_animal_seconds": self._missing_animal_seconds,
                    },
                )
                continue

            readings = animal.get_readings_in_window()
            if not readings:
                self._handle_no_evidence(
                    animal_id=animal.animal_id,
                    now=now,
                    reason="no readings available",
                )
                continue
            window_readings = [
                r
                for r in readings
                if r.timestamp >= cutoff and (not self._device_names or getattr(r, "device_name", None) in self._device_names)
            ]
            if not window_readings:
                self._handle_no_evidence(
                    animal_id=animal.animal_id,
                    now=now,
                    reason="no readings in classifier input window",
                )
                continue

            readings_payload = [
                {
                    "timestamp": r.timestamp,
                    "temperature": r.temperature,
                    "zone": r.zone,
                    "packet_number": r.packet_number,
                    "device_name": getattr(r, "device_name", None),
                }
                for r in window_readings
            ]

            result = self._classifier(
                animal.animal_id,
                readings_payload,
                now,
                self._classifier_config,
            )
            event = self._build_event_for_mode(
                animal_id=animal.animal_id,
                result=result,
                now=now,
            )
            self._update_status(
                animal_id=animal.animal_id,
                result=result if isinstance(result, dict) else None,
                event=event,
                now=now,
            )
            if event and self._on_trigger:
                self._on_trigger(event)

    def _handle_no_evidence(
        self,
        animal_id: str,
        now: datetime,
        reason: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._trigger_mode != "window":
            return
        result = {
            "trigger": False,
            "condition_true": False,
            "action": "stop",
            "stimulus_id": str(self._classifier_config.get("stimulus_id", "")),
            "reason": reason,
            "meta": {
                "animal_id": animal_id,
                "count": 0,
                "coverage_ready": False,
                "threshold_met": False,
                **(meta or {}),
            },
        }
        event = self._build_event_for_mode(
            animal_id=animal_id,
            result=result,
            now=now,
        )
        self._update_status(
            animal_id=animal_id,
            result=result,
            event=event,
            now=now,
        )
        if event and self._on_trigger:
            self._on_trigger(event)

    def _build_event_for_mode(
        self,
        animal_id: str,
        result: Any,
        now: datetime,
    ) -> Optional[TriggerEvent]:
        result_dict = result if isinstance(result, dict) else {}
        condition_true = bool(result_dict.get("condition_true", result_dict.get("trigger", False)))
        stimulus_id = str(result_dict.get("stimulus_id", self._classifier_config.get("stimulus_id", "")))

        if self._trigger_mode == "window":
            prev = self._window_state.get(animal_id, False)
            self._window_state[animal_id] = condition_true
            if condition_true and not prev:
                return TriggerEvent(
                    animal_event_id=None,
                    animal_id=animal_id,
                    rule_id=self._rule_id,
                    action="start",
                    stimulus_id=stimulus_id,
                    reason=str(result_dict.get("reason", "condition true")),
                    meta=self._build_event_meta(result_dict),
                    timestamp=now,
                )
            if (not condition_true) and prev:
                return TriggerEvent(
                    animal_event_id=None,
                    animal_id=animal_id,
                    rule_id=self._rule_id,
                    action="stop",
                    stimulus_id=stimulus_id,
                    reason=str(result_dict.get("reason", "condition false")),
                    meta=self._build_event_meta(result_dict),
                    timestamp=now,
                )
            return None

        if not result_dict.get("trigger"):
            return None
        return TriggerEvent(
            animal_event_id=None,
            animal_id=animal_id,
            rule_id=self._rule_id,
            action=str(result_dict.get("action", "pulse")),
            stimulus_id=stimulus_id,
            reason=str(result_dict.get("reason", "")),
            meta=self._build_event_meta(result_dict),
            timestamp=now,
        )

    def _update_status(
        self,
        animal_id: str,
        result: Optional[Dict[str, Any]],
        event: Optional[TriggerEvent],
        now: datetime,
    ) -> None:
        result_dict = result or {}
        meta = result_dict.get("meta", {})
        with self._status_lock:
            prev = self._status_by_animal.get(f"{self._rule_id}:{animal_id}")
        trigger_count = (prev.trigger_count if prev else 0) + (1 if event else 0)
        status = TriggerAnimalStatus(
            rule_id=self._rule_id,
            animal_id=animal_id,
            device_names=list(self._device_names),
            target_channels=list(self._target_channels),
            condition_true=bool(result_dict.get("condition_true", result_dict.get("trigger", False))),
            current_avg_temp=(float(meta["avg_temp"]) if isinstance(meta, dict) and "avg_temp" in meta else None),
            sample_count=(int(meta["count"]) if isinstance(meta, dict) and "count" in meta else 0),
            observed_duration_seconds=(
                float(meta["observed_duration_seconds"])
                if isinstance(meta, dict) and meta.get("observed_duration_seconds") is not None
                else None
            ),
            required_duration_seconds=(
                float(meta["required_duration_seconds"])
                if isinstance(meta, dict) and meta.get("required_duration_seconds") is not None
                else None
            ),
            coverage_tolerance_seconds=(
                float(meta["coverage_tolerance_seconds"])
                if isinstance(meta, dict) and meta.get("coverage_tolerance_seconds") is not None
                else None
            ),
            threshold_c=(
                float(meta["threshold_c"])
                if isinstance(meta, dict) and meta.get("threshold_c") is not None
                else None
            ),
            direction=str(meta.get("direction", "")) if isinstance(meta, dict) else "",
            aggregation=str(meta.get("aggregation", "")) if isinstance(meta, dict) else "",
            threshold_met=(
                bool(meta["threshold_met"])
                if isinstance(meta, dict) and meta.get("threshold_met") is not None
                else None
            ),
            coverage_ready=(
                bool(meta["coverage_ready"])
                if isinstance(meta, dict) and meta.get("coverage_ready") is not None
                else None
            ),
            min_samples=(
                int(meta["min_samples"])
                if isinstance(meta, dict) and meta.get("min_samples") is not None
                else None
            ),
            last_action=(event.action if event else (prev.last_action if prev else "none")),
            stimulus_id=str(result_dict.get("stimulus_id", self._classifier_config.get("stimulus_id", ""))),
            reason=str(result_dict.get("reason", prev.reason if prev else "")),
            last_eval_time=now,
            last_event_time=(event.timestamp if event else (prev.last_event_time if prev else None)),
            trigger_count=trigger_count,
        )
        with self._status_lock:
            self._status_by_animal[f"{self._rule_id}:{animal_id}"] = status

    def _build_event_meta(self, result_dict: Dict[str, Any]) -> Dict[str, Any]:
        meta = dict(result_dict.get("meta", {}))
        if self._rule_id:
            meta.setdefault("rule_id", self._rule_id)
        if self._target_channels and "channels" not in meta:
            meta["channels"] = list(self._target_channels)
        if self._device_names and "devices" not in meta:
            meta["devices"] = list(self._device_names)
        return meta

"""Duration-aware threshold classifier plugin."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _matches_threshold(value: float, threshold: float, direction: str) -> bool:
    if direction == "above":
        return value > threshold
    return value < threshold


def evaluate(
    animal_id: str,
    window_readings: List[Dict[str, Any]],
    now: datetime,
    config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Trigger only after enough covered time satisfies a threshold rule.

    Config keys:
        - threshold_c: float
        - direction: "below" or "above", default "below"
        - required_duration_seconds: float
        - min_samples: int
        - min_coverage_fraction: float, default 1.0
        - coverage_tolerance_seconds: float, default 1.0
        - aggregation: "mean", "all", or "fraction"
        - min_fraction_true: float, used when aggregation="fraction"
    """
    if not window_readings:
        return None

    threshold = _as_float(config.get("threshold_c"), 35.0)
    direction = str(config.get("direction", "below")).strip().lower()
    if direction not in {"below", "above"}:
        direction = "below"
    required_duration = max(
        0.0,
        _as_float(config.get("required_duration_seconds", config.get("duration_seconds")), 0.0),
    )
    min_samples = max(1, _as_int(config.get("min_samples"), 1))
    min_coverage_fraction = _as_float(config.get("min_coverage_fraction"), 1.0)
    min_coverage_fraction = min(1.0, max(0.0, min_coverage_fraction))
    coverage_tolerance_seconds = max(0.0, _as_float(config.get("coverage_tolerance_seconds"), 1.0))
    aggregation = str(config.get("aggregation", "mean")).strip().lower()
    if aggregation not in {"mean", "all", "fraction"}:
        aggregation = "mean"
    min_fraction_true = _as_float(config.get("min_fraction_true"), 1.0)
    min_fraction_true = min(1.0, max(0.0, min_fraction_true))

    sorted_readings = sorted(window_readings, key=lambda r: r["timestamp"])
    input_count = len(sorted_readings)
    if required_duration > 0:
        latest_input = sorted_readings[-1]["timestamp"]
        evidence_cutoff = latest_input - timedelta(seconds=required_duration)
        sorted_readings = [r for r in sorted_readings if r["timestamp"] >= evidence_cutoff]
        if not sorted_readings:
            return None
    temps = [float(r["temperature"]) for r in sorted_readings]
    avg_temp = sum(temps) / len(temps)
    earliest = sorted_readings[0]["timestamp"]
    latest = sorted_readings[-1]["timestamp"]
    observed_duration = max(0.0, (latest - earliest).total_seconds())
    required_coverage = required_duration * min_coverage_fraction
    coverage_ready = (observed_duration + coverage_tolerance_seconds) >= required_coverage and len(temps) >= min_samples

    matches = [_matches_threshold(temp, threshold, direction) for temp in temps]
    fraction_true = sum(1 for item in matches if item) / len(matches)
    if aggregation == "all":
        threshold_met = all(matches)
    elif aggregation == "fraction":
        threshold_met = fraction_true >= min_fraction_true
    else:
        threshold_met = _matches_threshold(avg_temp, threshold, direction)

    condition_true = coverage_ready and threshold_met
    stimulus_id = str(config.get("stimulus_id", f"{direction}_{threshold:g}c"))
    if not coverage_ready:
        if len(temps) < min_samples:
            reason = "waiting for samples"
        else:
            reason = "collecting window"
    elif threshold_met:
        reason = f"window ready; {aggregation} {direction} threshold"
    else:
        reason = f"window ready; {aggregation} not {direction} threshold"

    return {
        "trigger": condition_true,
        "condition_true": condition_true,
        "action": str(config.get("action", "pulse")),
        "stimulus_id": stimulus_id,
        "reason": reason,
        "meta": {
            "avg_temp": avg_temp,
            "count": len(temps),
            "input_count": input_count,
            "threshold_c": threshold,
            "direction": direction,
            "aggregation": aggregation,
            "fraction_true": fraction_true,
            "threshold_met": threshold_met,
            "coverage_ready": coverage_ready,
            "observed_duration_seconds": observed_duration,
            "required_duration_seconds": required_duration,
            "coverage_tolerance_seconds": coverage_tolerance_seconds,
            "required_coverage_seconds": required_coverage,
            "min_samples": min_samples,
            "animal_id": animal_id,
        },
    }

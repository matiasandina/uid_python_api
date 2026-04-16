"""Example classifier plugin.

Implements the expected evaluate() signature.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime


def evaluate(
    animal_id: str,
    window_readings: List[Dict[str, Any]],
    now: datetime,
    config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Example classifier: trigger if average temp is below a threshold.

    Config keys:
        - threshold_c: float
    """
    if not window_readings:
        return None

    threshold = float(config.get("threshold_c", 35.0))
    temps = [r["temperature"] for r in window_readings]
    avg_temp = sum(temps) / len(temps)

    if avg_temp < threshold:
        return {
            "trigger": True,
            "condition_true": True,
            "action": "pulse",
            "stimulus_id": config.get("stimulus_id", "default"),
            "reason": f"avg_temp {avg_temp:.2f} < {threshold}",
            "meta": {"avg_temp": avg_temp, "count": len(temps)},
        }

    return {
        "trigger": False,
        "condition_true": False,
        "stimulus_id": config.get("stimulus_id", "default"),
        "reason": f"avg_temp {avg_temp:.2f} >= {threshold}",
        "meta": {"avg_temp": avg_temp, "count": len(temps)},
    }

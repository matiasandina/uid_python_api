"""Causal temperature cleaning for closed-loop classifier input.

Raw acquisition data is not modified by this module. It operates on the
classifier payload copy immediately before trigger evaluation.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple


PROBE_MIN_SENTINEL_C = 25.0
PROBE_MAX_SENTINEL_C = 50.0
PHYSIOLOGIC_HIGH_C = 45.0
CONTEXT_HORIZON_SECONDS = 60.0
MIN_CONTEXT_SAMPLES = 3
MIN_OUTLIER_DELTA_C = 2.0
ROBUST_SIGMA_MULTIPLIER = 8.0
MIN_ROBUST_SIGMA_C = 0.2


@dataclass(frozen=True)
class FilterSummary:
    raw_count: int
    cleaned_count: int
    corrected_count: int
    dropped_count: int
    probe_gate_count: int
    high_sanity_count: int
    local_outlier_count: int

    def as_dict(self) -> Dict[str, int]:
        return {
            "raw_count": self.raw_count,
            "cleaned_count": self.cleaned_count,
            "corrected_count": self.corrected_count,
            "dropped_count": self.dropped_count,
            "probe_gate_count": self.probe_gate_count,
            "high_sanity_count": self.high_sanity_count,
            "local_outlier_count": self.local_outlier_count,
        }


def _as_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _median_abs_deviation(values: List[float], center: float) -> float:
    if not values:
        return 0.0
    return statistics.median(abs(value - center) for value in values)


def _prune_context(
    context: Deque[Dict[str, Any]],
    timestamp: datetime,
    horizon_seconds: float,
) -> None:
    cutoff = timestamp - timedelta(seconds=horizon_seconds)
    while context and context[0]["timestamp"] < cutoff:
        context.popleft()


def _predict_from_context(context: List[Dict[str, Any]]) -> Optional[Tuple[float, float]]:
    if not context:
        return None
    temps = [float(item["temperature"]) for item in context]
    prediction = statistics.median(temps)
    mad = _median_abs_deviation(temps, prediction)
    robust_sigma = max(MIN_ROBUST_SIGMA_C, 1.4826 * mad)
    return prediction, robust_sigma


def _replacement_from_context(context: List[Dict[str, Any]]) -> Optional[float]:
    if not context:
        return None
    return float(context[-1]["temperature"])


def _probe_gate_reason(temperature: Optional[float]) -> Optional[str]:
    if temperature is None:
        return "non_finite"
    if temperature <= PROBE_MIN_SENTINEL_C:
        return "probe_min_sentinel"
    if temperature >= PROBE_MAX_SENTINEL_C:
        return "probe_max_sentinel"
    return None


def _local_outlier_reason(
    temperature: float,
    context: List[Dict[str, Any]],
    prediction: float,
    robust_sigma: float,
) -> Optional[str]:
    if not context:
        return None
    delta = abs(temperature - prediction)
    threshold = max(MIN_OUTLIER_DELTA_C, ROBUST_SIGMA_MULTIPLIER * robust_sigma)
    if delta <= threshold:
        return None

    if len(context) >= MIN_CONTEXT_SAMPLES:
        return "local_robust_outlier"

    previous = context[-1]
    previous_delta = abs(temperature - float(previous["temperature"]))
    if previous_delta >= max(3.0, threshold):
        return "fresh_single_step_outlier"
    return None


def clean_classifier_temperatures(
    readings: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], FilterSummary]:
    """Return a cleaned classifier payload using only past samples as context."""
    ordered = sorted((dict(item) for item in readings), key=lambda item: item["timestamp"])
    accepted: List[Dict[str, Any]] = []
    recent_context: Deque[Dict[str, Any]] = deque()
    corrected_count = 0
    dropped_count = 0
    probe_gate_count = 0
    high_sanity_count = 0
    local_outlier_count = 0

    for item in ordered:
        timestamp = item["timestamp"]
        raw_temperature = _as_float(item.get("temperature"))
        _prune_context(recent_context, timestamp, CONTEXT_HORIZON_SECONDS)
        context = list(recent_context)
        prediction = _predict_from_context(context)
        reason = _probe_gate_reason(raw_temperature)
        if reason is not None:
            probe_gate_count += 1
        elif raw_temperature is not None and raw_temperature > PHYSIOLOGIC_HIGH_C:
            reason = "physiologic_high"
            high_sanity_count += 1
        elif raw_temperature is not None and prediction is not None:
            reason = _local_outlier_reason(raw_temperature, context, prediction[0], prediction[1])
            if reason is not None:
                local_outlier_count += 1

        if reason is None and raw_temperature is not None:
            item["temperature"] = raw_temperature
            accepted.append(item)
            recent_context.append(item)
            continue

        replacement = _replacement_from_context(context)
        if prediction is None or replacement is None:
            dropped_count += 1
            continue

        cleaned = dict(item)
        cleaned["temperature"] = replacement
        cleaned["temperature_filter"] = {
            "reason": reason,
            "raw_temperature": item.get("temperature"),
            "replacement_temperature": replacement,
            "local_prediction_temperature": prediction[0],
            "context_count": len(context),
            "context_horizon_seconds": CONTEXT_HORIZON_SECONDS,
        }
        accepted.append(cleaned)
        recent_context.append(cleaned)
        corrected_count += 1

    summary = FilterSummary(
        raw_count=len(ordered),
        cleaned_count=len(accepted),
        corrected_count=corrected_count,
        dropped_count=dropped_count,
        probe_gate_count=probe_gate_count,
        high_sanity_count=high_sanity_count,
        local_outlier_count=local_outlier_count,
    )
    return accepted, summary


def _parse_timestamp(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return datetime.fromisoformat(value)


def _load_csv(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for idx, row in enumerate(reader, start=1):
            timestamp_value = row.get("DateTime") or row.get("timestamp") or row.get("timestamp_utc")
            temperature_value = row.get("Temperature") or row.get("temperature") or row.get("temperature_c")
            uid = row.get("UID") or row.get("animal_id") or ""
            if not timestamp_value or temperature_value is None:
                continue
            rows.append(
                {
                    "timestamp": _parse_timestamp(timestamp_value),
                    "temperature": float(temperature_value),
                    "animal_id": uid,
                    "zone": row.get("Zone") or row.get("zone"),
                    "packet_number": idx,
                    "device_name": path.stem,
                }
            )
    return rows


def _format_context(rows: List[Dict[str, Any]], index: int, radius: int = 3) -> List[str]:
    start = max(0, index - radius)
    end = min(len(rows), index + radius + 1)
    lines = []
    for offset in range(start, end):
        row = rows[offset]
        mark = "*" if offset == index else " "
        raw = row.get("raw_temperature", row.get("temperature"))
        cleaned = row.get("temperature")
        reason = row.get("filter_reason", "")
        lines.append(f"{mark} {row['timestamp']} raw={raw:.1f} cleaned={cleaned:.1f} {reason}")
    return lines


def run_csv_diagnostic(paths: List[Path], limit: int) -> int:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for path in paths:
        for row in _load_csv(path):
            key = str(row.get("animal_id") or path.stem)
            grouped.setdefault(key, []).append(row)

    for animal_id, rows in sorted(grouped.items()):
        rows.sort(key=lambda item: item["timestamp"])
        cleaned, summary = clean_classifier_temperatures(rows)
        cleaned_by_key = {
            (item["timestamp"], item.get("packet_number"), item.get("device_name")): item
            for item in cleaned
        }
        display_rows: List[Dict[str, Any]] = []
        diagnostic_indices: List[int] = []
        for idx, row in enumerate(rows):
            key = (row["timestamp"], row.get("packet_number"), row.get("device_name"))
            cleaned_row = cleaned_by_key.get(key)
            display_row = {
                **row,
                "raw_temperature": float(row["temperature"]),
                "filter_reason": "",
            }
            if cleaned_row and "temperature_filter" in cleaned_row:
                display_row["temperature"] = float(cleaned_row["temperature"])
                display_row["filter_reason"] = cleaned_row["temperature_filter"]["reason"]
                diagnostic_indices.append(idx)
            display_rows.append(display_row)

        print(f"\n{animal_id}: {summary.as_dict()}")
        for idx in diagnostic_indices[:limit]:
            row = display_rows[idx]
            for line in _format_context(display_rows, idx):
                print(line)
            print(f"  replacement: {row['raw_temperature']:.1f} -> {row['temperature']:.1f} ({row['filter_reason']})")
            print("")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect causal classifier temperature filtering on CSV files.")
    parser.add_argument("csv", nargs="+", type=Path)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    return run_csv_diagnostic(args.csv, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())

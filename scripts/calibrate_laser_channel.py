#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from doric_light_source import ChannelHardwareConfig, DoricLightSource, LightSourceSquareConfig
from typed_config import load_config


DEFAULT_CURRENTS = [30, 40, 50, 60, 70, 80]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manual channel-by-channel Doric laser calibration for continuous output.",
    )
    parser.add_argument("--channel", required=True, help="Channel name, for example ch1.")
    parser.add_argument("--target-mw", type=float, default=10.0, help="Target continuous output power in mW.")
    parser.add_argument(
        "--currents",
        type=int,
        nargs="+",
        default=DEFAULT_CURRENTS,
        help="Current sweep in mA. Default: 30 40 50 60 70 80",
    )
    parser.add_argument(
        "--duration-sec",
        type=float,
        default=None,
        help="Optional auto-stop duration per step. If omitted, output stays on until you press Enter.",
    )
    parser.add_argument("--config", default=None, help="Optional overlay YAML to merge over config.local.yaml.")
    parser.add_argument("--local-config", default="config.local.yaml", help="Machine-local config path.")
    return parser.parse_args()


def _coerce_float(value: object, default: float) -> float:
    if value is None:
        return default
    return float(value)


def _coerce_int(value: object, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _coerce_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _resolve_channel_config(config: Dict[str, object], channel_name: str) -> ChannelHardwareConfig:
    channels_cfg = config.get("stimulus", {}).get("channels", {})
    if isinstance(channels_cfg, dict) and channel_name in channels_cfg:
        entry = channels_cfg[channel_name]
        if isinstance(entry, dict):
            return ChannelHardwareConfig(index=int(entry["index"]), current_ma=int(entry.get("current_ma", 0)))
    normalized = channel_name.strip().lower()
    if normalized.startswith("ch") and normalized[2:].isdigit():
        idx = int(normalized[2:])
        if 1 <= idx <= 8:
            return ChannelHardwareConfig(index=idx - 1, current_ma=0)
    raise ValueError(
        f"Could not resolve channel '{channel_name}'. Define it under stimulus.channels or use a name like ch1."
    )


def _resolve_laser(config: Dict[str, object], channel_name: str) -> DoricLightSource:
    stimulus = config["stimulus"]
    dll_path = str(stimulus.get("dll_path") or "").strip()
    uid = stimulus.get("uid")
    port = stimulus.get("port")
    if not dll_path:
        raise ValueError("stimulus.dll_path is required in config.local.yaml for calibration.")
    if not uid and port is None:
        raise ValueError("Set either stimulus.uid or stimulus.port in config.local.yaml for calibration.")
    channel_cfg = _resolve_channel_config(config, channel_name)
    square_cfg = stimulus.get("square", {})
    square = LightSourceSquareConfig(
        period_ms=_coerce_float(square_cfg.get("period_ms"), 100.0),
        time_on_ms=_coerce_float(square_cfg.get("time_on_ms"), 50.0),
        nb_of_seq=_coerce_int(square_cfg.get("nb_of_seq"), 0),
        nb_of_pulses_per_seq=_coerce_int(square_cfg.get("nb_of_pulses_per_seq"), 0),
        starting_delay_ms=_coerce_int(square_cfg.get("starting_delay_ms"), 0),
        delay_between_seq_ms=_coerce_int(square_cfg.get("delay_between_seq_ms"), 0),
        ttl_output=_coerce_bool(square_cfg.get("ttl_output"), False),
    )
    return DoricLightSource(
        dll_path=dll_path,
        port=int(port) if port is not None else None,
        uid=str(uid) if uid is not None else None,
        channels={channel_name: channel_cfg},
        square=square,
        verbose=False,
    )


def _prompt_yes_no(prompt: str) -> bool:
    while True:
        reply = input(f"{prompt} [y/N]: ").strip().lower()
        if reply in {"y", "yes"}:
            return True
        if reply in {"", "n", "no"}:
            return False
        print("Please answer y or n.")


def _prompt_power_mw(current_ma: int) -> float:
    while True:
        raw = input(f"Measured power for {current_ma} mA (mW): ").strip()
        try:
            value = float(raw)
        except ValueError:
            print("Enter a numeric power value in mW.")
            continue
        if value < 0:
            print("Power must be >= 0 mW.")
            continue
        return value


def _prompt_live_power_mw(current_ma: int) -> float:
    while True:
        raw = input(
            f"Measured power for {current_ma} mA (mW), or press Enter to keep holding: "
        ).strip()
        if raw == "":
            continue
        try:
            value = float(raw)
        except ValueError:
            print("Enter a numeric power value in mW, or press Enter to keep holding.")
            continue
        if value < 0:
            print("Power must be >= 0 mW.")
            continue
        return value


def _fit_linear(points: Sequence[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    if len(points) < 2 or len(set(xs)) < 2:
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _fit_power(points: Sequence[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    positive = [(x, y) for x, y in points if x > 0 and y > 0]
    if len(positive) < 2:
        return None
    log_points = [(math.log(x), math.log(y)) for x, y in positive]
    fitted = _fit_linear(log_points)
    if fitted is None:
        return None
    slope, intercept = fitted
    a = math.exp(intercept)
    b = slope
    return a, b


def _solve_linear(target_mw: float, fitted: Optional[Tuple[float, float]]) -> Optional[float]:
    if fitted is None:
        return None
    slope, intercept = fitted
    if slope == 0:
        return None
    return (target_mw - intercept) / slope


def _solve_power(target_mw: float, fitted: Optional[Tuple[float, float]]) -> Optional[float]:
    if fitted is None:
        return None
    a, b = fitted
    if a <= 0 or b == 0 or target_mw <= 0:
        return None
    return (target_mw / a) ** (1.0 / b)


def _format_solution(label: str, current_ma: Optional[float], min_current: int, max_current: int) -> str:
    if current_ma is None or not math.isfinite(current_ma):
        return f"{label}: unavailable"
    range_note = ""
    if current_ma < min_current or current_ma > max_current:
        range_note = " (outside sampled range)"
    return f"{label}: {current_ma:.2f} mA{range_note}"


def _print_summary(
    channel_name: str,
    target_mw: float,
    measurements: Sequence[Tuple[int, float]],
    linear_fit: Optional[Tuple[float, float]],
    power_fit: Optional[Tuple[float, float]],
) -> None:
    print()
    print(f"Calibration summary for {channel_name}")
    print("current_mA\tpower_mW")
    for current_ma, power_mw in measurements:
        print(f"{current_ma}\t{power_mw:.4f}")

    min_current = min(current for current, _ in measurements)
    max_current = max(current for current, _ in measurements)
    linear_solution = _solve_linear(target_mw, linear_fit)
    power_solution = _solve_power(target_mw, power_fit)

    print()
    if linear_fit is None:
        print("Linear fit: unavailable")
    else:
        slope, intercept = linear_fit
        print(f"Linear fit: power_mW = {slope:.6f} * current_mA + {intercept:.6f}")
    if power_fit is None:
        print("Power fit: unavailable")
    else:
        a, b = power_fit
        print(f"Power fit: power_mW = {a:.6f} * current_mA^{b:.6f}")

    print(_format_solution(f"Linear solve for {target_mw:g} mW", linear_solution, min_current, max_current))
    print(_format_solution(f"Power solve for {target_mw:g} mW", power_solution, min_current, max_current))


def _run_step(
    laser: DoricLightSource,
    channel_name: str,
    current_ma: int,
    duration_sec: Optional[float],
) -> Optional[float]:
    laser.stop_channel(channel_name)
    laser.configure_channel(channel_name, current_ma=current_ma, mode="cw")
    laser.start_channel(channel_name)
    try:
        if duration_sec is None:
            return _prompt_live_power_mw(current_ma)
        print(f"Output active at {current_ma} mA for {duration_sec:g} s...")
        time.sleep(duration_sec)
        return None
    finally:
        laser.stop_channel(channel_name)


def main() -> int:
    args = _parse_args()
    config = load_config(args.config, local_path=args.local_config, require_local=True)
    currents = [int(value) for value in args.currents]
    if len(currents) < 2:
        raise ValueError("Provide at least two current values for fitting.")
    if any(value <= 0 for value in currents):
        raise ValueError("All current values must be > 0 mA.")
    if args.target_mw <= 0:
        raise ValueError("--target-mw must be > 0.")
    if args.duration_sec is not None and args.duration_sec <= 0:
        raise ValueError("--duration-sec must be > 0.")

    laser = _resolve_laser(config, args.channel)
    print(f"Channel: {args.channel}")
    print(f"Target: {args.target_mw:g} mW continuous output")
    print(f"Currents (mA): {' '.join(str(value) for value in currents)}")
    if args.duration_sec is None:
        print("Duration per step: manual stop")
    else:
        print(f"Duration per step: {args.duration_sec:g} s")
    print("Mode: CW / continuous illumination")
    print()
    print("This script emits real laser output.")
    print("Use only with no animal present, protective eyewear, and a safe beam path into the sensor.")
    if not _prompt_yes_no("Proceed with calibration"):
        print("Calibration canceled.")
        return 1

    measurements: List[Tuple[int, float]] = []
    laser.connect()
    try:
        for current_ma in currents:
            print()
            if args.duration_sec is None:
                print(f"Running {args.channel} at {current_ma} mA until manual stop...")
            else:
                print(f"Running {args.channel} at {current_ma} mA for {args.duration_sec:g} s...")
            power_mw = _run_step(laser, args.channel, current_ma, args.duration_sec)
            if power_mw is None:
                power_mw = _prompt_power_mw(current_ma)
            measurements.append((current_ma, power_mw))
    finally:
        try:
            laser.stop_all()
        finally:
            laser.close()

    linear_fit = _fit_linear(measurements)
    power_fit = _fit_power(measurements)
    _print_summary(args.channel, args.target_mw, measurements, linear_fit, power_fit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

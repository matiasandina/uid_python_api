#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ttl_capture.protocol import EXPECTED_FRAME_SIZE, EXPECTED_SAMPLE_RATE_HZ
from ttl_capture.reader import TeensyTTLReader


CHANNELS = 4


@dataclass
class TTLMonitorStats:
    frame_count: int = 0
    sample_count: int = 0
    high_counts: List[int] = field(default_factory=lambda: [0] * CHANNELS)
    low_counts: List[int] = field(default_factory=lambda: [0] * CHANNELS)
    transitions: List[int] = field(default_factory=lambda: [0] * CHANNELS)
    last_sample: int | None = None

    def update(self, sample: int) -> None:
        for ch in range(CHANNELS):
            bit = (sample >> ch) & 0x01
            if bit:
                self.high_counts[ch] += 1
            else:
                self.low_counts[ch] += 1

            if self.last_sample is not None:
                prev_bit = (self.last_sample >> ch) & 0x01
                if bit != prev_bit:
                    self.transitions[ch] += 1

        self.sample_count += 1
        self.last_sample = sample

    def update_frame(self, payload: bytes) -> None:
        self.frame_count += 1
        for sample in payload:
            self.update(sample)


def format_mask(sample: int) -> str:
    return " ".join(f"CH{ch}={(sample >> ch) & 0x01}" for ch in range(CHANNELS))


def print_summary(stats: TTLMonitorStats) -> None:
    print("")
    print("Summary")
    print(f"Frames: {stats.frame_count}")
    print(f"Samples: {stats.sample_count}")
    for ch in range(CHANNELS):
        print(
            f"CH{ch}: high={stats.high_counts[ch]} low={stats.low_counts[ch]} transitions={stats.transitions[ch]}"
        )


def iter_raw_file(path: Path, frame_size: int) -> Iterable[bytes]:
    with path.open("rb") as handle:
        while True:
            payload = handle.read(frame_size)
            if not payload:
                break
            yield payload


def run_live(port: str, baudrate: int, status_interval: float) -> int:
    reader = TeensyTTLReader(port=port, baudrate=baudrate)
    stats = TTLMonitorStats()
    last_status = 0.0

    reader.open()
    try:
        handshake = reader.read_handshake()
        print(
            "Handshake:",
            f"rate={handshake.sampling_rate_hz}Hz",
            f"frame_size={handshake.frame_size}",
            f"fw={handshake.firmware_version}",
            f"git={handshake.git_hash}",
        )
        print("Raw GPIO semantics: 1=pin HIGH, 0=pin LOW. With your H11L1 board, asserted TTL should read 0.")

        for frame in reader.iter_frames():
            stats.update_frame(frame.payload)

            if frame.payload:
                now = time.monotonic()
                if now - last_status >= status_interval:
                    current = frame.payload[-1]
                    print(
                        f"frame={frame.frame_id} t_us={frame.t_us_first_sample} raw={current:04b} {format_mask(current)}"
                    )
                    last_status = now
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()

    print_summary(stats)
    return 0


def run_raw_file(path: Path, frame_size: int) -> int:
    stats = TTLMonitorStats()

    for payload in iter_raw_file(path, frame_size):
        stats.update_frame(payload)

    print(f"Analyzed raw payload file: {path}")
    print_summary(stats)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live monitor or offline summary for Teensy TTL raw GPIO bitmasks."
    )
    parser.add_argument("--port", help="Serial port for live monitoring, e.g. /dev/ttyACM0 or COM7")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument(
        "--status-interval",
        type=float,
        default=0.5,
        help="Seconds between live status lines in serial mode",
    )
    parser.add_argument("--raw-file", type=Path, help="Offline mode: analyze a ttl_raw.bin file")
    parser.add_argument(
        "--frame-size",
        type=int,
        default=EXPECTED_FRAME_SIZE,
        help="Frame size for offline raw-file mode",
    )
    parser.add_argument(
        "--sample-rate-hz",
        type=int,
        default=EXPECTED_SAMPLE_RATE_HZ,
        help="Accepted for visibility; raw summary does not currently use timing",
    )
    args = parser.parse_args()

    if bool(args.port) == bool(args.raw_file):
        parser.error("Choose exactly one of --port or --raw-file.")

    if args.frame_size <= 0:
        parser.error("--frame-size must be > 0")
    if args.sample_rate_hz <= 0:
        parser.error("--sample-rate-hz must be > 0")

    if args.port:
        return run_live(port=args.port, baudrate=args.baudrate, status_interval=max(0.1, args.status_interval))

    return run_raw_file(path=args.raw_file, frame_size=args.frame_size)


if __name__ == "__main__":
    raise SystemExit(main())

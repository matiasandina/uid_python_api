#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ttl_capture.capture import TTLCaptureService


def main() -> int:
    parser = argparse.ArgumentParser(description="Record Teensy TTL stream to ttl_raw.bin + ttl_meta.json")
    parser.add_argument("--port", required=True, help="Serial port (e.g., COM7 or /dev/ttyACM0)")
    parser.add_argument("--duration", type=float, default=10.0, help="Recording duration in seconds")
    parser.add_argument("--output-dir", default="./ttl_recordings", help="Destination folder")
    parser.add_argument("--baudrate", type=int, default=115200)
    args = parser.parse_args()

    timestamp = time.strftime("%Y_%m_%d_%H_%M_%S")
    session_dir = Path(args.output_dir) / f"{timestamp}_ttl_record"

    capture = TTLCaptureService(
        session_folder=str(session_dir),
        port=args.port,
        baudrate=args.baudrate,
    )

    capture.start()
    deadline = time.monotonic() + max(0.1, args.duration)
    try:
        while time.monotonic() < deadline:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        capture.stop()

    status = capture.status_dict()
    print(f"Session folder: {session_dir}")
    print(f"Frames received: {status['frames_received']}")
    print(f"Dropped frames: {status['dropped_frames']}")
    print(f"Bytes written: {status['bytes_written']}")
    if status.get("last_error"):
        print(f"Last error: {status['last_error']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

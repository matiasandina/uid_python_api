#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ttl_capture.session_verify import format_report, verify_session


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify commanded stimulation windows against TTL capture artifacts.")
    parser.add_argument("--session-metadata", required=True, help="Path to session metadata, typically data/<session>/session.yaml")
    parser.add_argument(
        "--tolerance-ms",
        type=float,
        default=100.0,
        help="Boundary tolerance when matching TTL edges to commanded windows",
    )
    args = parser.parse_args()

    report = verify_session(args.session_metadata, tolerance_ms=args.tolerance_ms)
    print(format_report(report))
    return 0 if not report.issues and report.windows_verified > 0 and report.windows_ok == report.windows_verified else 1


if __name__ == "__main__":
    raise SystemExit(main())

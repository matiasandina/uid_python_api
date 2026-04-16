#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from doric_light_source import (
    add_doric_dll_search_paths,
    close_dll_directory_handles,
    describe_doric_dll_search,
    load_doric_cdll,
    resolve_doric_dll_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose Doric DLL resolution and Windows dependency search paths. "
            "Accepts either an exact DoricSystem.dll path or a containing directory."
        )
    )
    parser.add_argument(
        "dll_path",
        help="Path to DoricSystem.dll or a directory containing it, matching stimulus.dll_path semantics.",
    )
    parser.add_argument(
        "--load",
        action="store_true",
        help="Attempt to load the resolved DLL with ctypes.CDLL. On non-Windows, this only reports what would happen.",
    )
    args = parser.parse_args()

    requested = Path(args.dll_path)
    print(f"requested_path: {requested}")
    print(f"platform: {os.name}")

    try:
        resolved = resolve_doric_dll_path(args.dll_path)
    except Exception as exc:
        print(f"resolve_error: {exc}")
        return 1

    print(f"resolved_dll: {resolved}")

    for line in describe_doric_dll_search(args.dll_path):
        print(line)

    if not args.load:
        print("load_skipped: pass --load to attempt ctypes.CDLL")
        return 0

    if os.name != "nt":
        print("load_skipped: ctypes load is only meaningful on Windows for this DLL")
        return 0

    handles = []
    try:
        handles = add_doric_dll_search_paths(resolved)
        print(f"added_search_dirs: {len(handles)}")
        lib = load_doric_cdll(resolved)
        print(f"load_ok: {lib}")
        return 0
    except Exception as exc:
        print(f"load_error: {exc}")
        return 2
    finally:
        close_dll_directory_handles(handles)


if __name__ == "__main__":
    raise SystemExit(main())

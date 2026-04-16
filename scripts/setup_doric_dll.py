#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from doric_light_source import DORIC_DLL_PACKAGE_URL, DEFAULT_DORIC_DLL_DIR, resolve_doric_dll_path


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response:
        destination.write_bytes(response.read())


def _archive_has_doric_dll(archive: Path) -> bool:
    with zipfile.ZipFile(archive) as zf:
        return any(Path(name).name == "DoricSystem.dll" for name in zf.namelist())


def _extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(destination)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download and unpack the DoricSystemDLL package into the repo-local, "
            "gitignored default DLL directory."
        )
    )
    parser.add_argument("--url", default=DORIC_DLL_PACKAGE_URL, help="Doric DLL zip URL.")
    parser.add_argument(
        "--destination",
        type=Path,
        default=DEFAULT_DORIC_DLL_DIR,
        help="Directory where the package should be unpacked.",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=DEFAULT_DORIC_DLL_DIR.with_suffix(".zip"),
        help="Downloaded zip archive path.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow extraction into a non-empty destination and overwrite files from the archive.",
    )
    args = parser.parse_args()

    destination = args.destination
    if destination.exists() and any(destination.iterdir()) and not args.force:
        print(f"destination_exists: {destination}")
        print("Refusing to unpack into a non-empty directory without --force.")
        try:
            print(f"resolved_dll: {resolve_doric_dll_path(str(destination))}")
            return 0
        except Exception as exc:
            print(f"resolve_error: {exc}")
            return 3

    print(f"download_url: {args.url}")
    print(f"archive: {args.archive}")
    _download(args.url, args.archive)

    if not _archive_has_doric_dll(args.archive):
        print("download_error: archive did not contain DoricSystem.dll")
        return 2

    print(f"destination: {destination}")
    _extract(args.archive, destination)

    try:
        resolved = resolve_doric_dll_path(str(destination))
    except Exception as exc:
        print(f"resolve_error: {exc}")
        return 3

    print(f"resolved_dll: {resolved}")
    print("config_default: stimulus.dll_path may be omitted when this default directory is used.")
    print(f"config_override: stimulus.dll_path: {destination}")
    print("next_check: uv run --python .venv/bin/python scripts/doric_imports_diag.py ./vendor/DoricSystemDLL")
    print("next_probe: uv run --python .venv/bin/python scripts/doric_probe.py --dll-path ./vendor/DoricSystemDLL --list-infos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

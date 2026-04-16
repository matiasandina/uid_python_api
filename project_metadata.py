"""Project identity, attribution, support text, and build metadata."""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

PROJECT_TITLE = "Real-Time UID Mouse Matrix Temperature Monitoring System"
APP_VERSION = "0.0.1"

PROJECT_SUMMARY = (
    "IP-connected temperature monitoring from UID Mouse Matrices, temperature logging, "
    "live UI, and stimulation control through the Doric laser API."
)

ACKNOWLEDGMENT = (
    "The TCP module builds on earlier contribution work by Derek Jordan. "
    "The current runtime, UI, stimulation, replay, TTL capture, and configuration system "
    "were created and are maintained by Matias Andina."
)

CLI_EPILOG = (
    "Acknowledgment:\n"
    "  TCP module contribution: Derek Jordan\n"
    "  Current runtime and maintenance: Matias Andina"
)


@lru_cache(maxsize=1)
def get_build_info() -> dict[str, str]:
    repo_root = Path(__file__).resolve().parent
    commit = "unknown"
    dirty = "unknown"
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
        )
        commit = out.decode("utf-8").strip() or "unknown"
    except Exception:
        commit = "unknown"
    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
        )
        dirty = "dirty" if status.decode("utf-8").strip() else "clean"
    except Exception:
        dirty = "unknown"
    return {
        "version": APP_VERSION,
        "commit": commit,
        "dirty": dirty,
    }


def get_build_label() -> str:
    info = get_build_info()
    return f"v{info['version']} | {info['commit']} | {info['dirty']}"

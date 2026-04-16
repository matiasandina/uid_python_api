from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict


def default_setup_state() -> Dict[str, Any]:
    return {
        "laser_state": "not_started",
        "laser_ready": False,
        "laser_selected_label": "",
        "laser_last_error": "",
        "bench_state": "not_tested",
        "bench_detail": "",
        "teensy_state": "not_started",
        "teensy_ready": False,
        "teensy_selected_label": "",
        "teensy_last_error": "",
    }


@dataclass
class LiveState:
    setup: Dict[str, Any] = field(default_factory=default_setup_state)
    laser_driver: Any = None
    launch_confirmed_at: datetime | None = None
    session_label: str | None = None

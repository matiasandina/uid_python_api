"""Session metadata writer for trigger/replay/sim runs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import yaml


RUNTIME_ONLY_KEYS = {"_setup_state", "_preflight_driver", "laser_driver"}


def _config_for_metadata(obj: Any) -> Any:
    if isinstance(obj, dict):
        cleaned: Dict[str, Any] = {}
        for key, value in obj.items():
            if str(key) in RUNTIME_ONLY_KEYS:
                continue
            cleaned[key] = _config_for_metadata(value)
        return cleaned
    if isinstance(obj, list):
        return [_config_for_metadata(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(_config_for_metadata(item) for item in obj)
    return obj


def _event_to_dict(event: Any) -> Dict[str, Any]:
    return {
        "animal_event_id": event.animal_event_id,
        "animal_id": event.animal_id,
        "rule_id": getattr(event, "rule_id", None),
        "action": event.action,
        "stimulus_id": event.stimulus_id,
        "reason": event.reason,
        "meta": dict(event.meta),
        "timestamp": event.timestamp.isoformat(),
    }


def _resolve_local_timezone(config: Dict[str, Any]) -> str | None:
    explicit = str(config.get("local_timezone") or "").strip()
    if explicit:
        return explicit

    stimulus = config.get("stimulus")
    if not isinstance(stimulus, dict):
        return None

    start_cfg = stimulus.get("start")
    if not isinstance(start_cfg, dict):
        return None

    timezone_name = str(start_cfg.get("timezone") or "").strip()
    return timezone_name or None


def write_session_metadata(
    output_directory: str,
    session_folder: str,
    config: Dict[str, Any],
    mode: str,
    start_time: datetime,
    end_time: datetime,
    trigger_events: list,
    missing_events: list,
    source: str | None = None,
    session_label: str | None = None,
    protocol_name: str | None = None,
    build_info: Dict[str, Any] | None = None,
) -> Path:
    session_dir = Path(session_folder)
    session_name = session_dir.name
    metadata_path = session_dir / "session.yaml"

    grouped_triggers: Dict[str, list] = {}
    for event in trigger_events:
        grouped_triggers.setdefault(event.animal_id, []).append(_event_to_dict(event))

    payload = {
        "session": {
            "session_folder": session_name,
            "session_label": session_label or None,
            "protocol_name": protocol_name or config.get("session_description"),
            "mode": mode,
            "local_timezone": _resolve_local_timezone(config),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "source": source,
        },
        "build": dict(build_info or {}),
        "config": _config_for_metadata(config),
        "triggers_by_animal": grouped_triggers,
        "missing_animals": [
            {
                "rule_id": m.get("rule_id"),
                "animal_id": m["animal_id"],
                "seconds_since": m["seconds_since"],
                "timestamp": m["timestamp"].isoformat(),
            }
            for m in missing_events
        ],
    }

    session_dir.mkdir(parents=True, exist_ok=True)
    with metadata_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)

    return metadata_path

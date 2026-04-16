from __future__ import annotations

from datetime import datetime
import re


def sanitize_session_label(label: str | None) -> str:
    if label is None:
        return ""
    cleaned = re.sub(r"[^0-9A-Za-z_-]+", "_", str(label).strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned


def build_session_folder_name(started_at: datetime, session_label: str | None) -> str:
    timestamp = started_at.strftime("%Y_%m_%d_%H_%M_%S")
    safe_label = sanitize_session_label(session_label)
    if not safe_label:
        return timestamp
    return f"{timestamp}_{safe_label}"

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def resolve_session_timezone(metadata: dict, override: str | None = None) -> ZoneInfo:
    if override:
        return ZoneInfo(str(override).strip())

    session = metadata.get("session", {}) if isinstance(metadata, dict) else {}
    config = metadata.get("config", {}) if isinstance(metadata, dict) else {}
    stimulus = config.get("stimulus", {}) if isinstance(config, dict) else {}
    start_cfg = stimulus.get("start", {}) if isinstance(stimulus, dict) else {}

    candidates = [
        session.get("local_timezone"),
        config.get("local_timezone"),
        start_cfg.get("timezone"),
    ]
    for candidate in candidates:
        if candidate and str(candidate).strip():
            return ZoneInfo(str(candidate).strip())

    raise ValueError(
        "Could not resolve a session timezone. Pass `--local-timezone`, "
        "or persist a timezone in session/config metadata."
    )


def parse_local_naive_timestamp(value: str, local_tz: ZoneInfo) -> tuple[datetime, datetime]:
    text = str(value).strip()
    if not text:
        raise ValueError("Timestamp value is empty.")

    local_dt: datetime
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f")

    if parsed.tzinfo is not None:
        local_dt = parsed.astimezone(local_tz)
    else:
        local_dt = parsed.replace(tzinfo=local_tz)

    return local_dt, local_dt.astimezone(timezone.utc)

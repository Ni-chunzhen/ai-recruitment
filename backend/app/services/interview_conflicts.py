from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def intervals_overlap(
    existing_start: datetime,
    existing_end: datetime,
    requested_start: datetime,
    requested_end: datetime,
) -> bool:
    return existing_start < requested_end and existing_end > requested_start


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("timezone is required")
    return value


def validate_iana_timezone(timezone_name: str) -> str:
    name = (timezone_name or "").strip()
    if not name:
        raise ValueError("timezone is required")
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("invalid IANA timezone") from exc
    return name


def validate_schedule_window(
    start_at_utc: datetime,
    end_at_utc: datetime,
    timezone_name: str,
) -> tuple[datetime, datetime, str]:
    tz = validate_iana_timezone(timezone_name)
    start = ensure_aware(start_at_utc)
    end = ensure_aware(end_at_utc)
    if end <= start:
        raise ValueError("end_at must be later than start_at")
    return start, end, tz

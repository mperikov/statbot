"""Дата/время начала учёта killfeed (часовой пояс из STATS_TIMEZONE)."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[misc, assignment]


DEFAULT_TIMEZONE = os.getenv("STATS_TIMEZONE", "Europe/Moscow")

# Fallback без пакета tzdata (типично Windows): постоянное смещение MSK = UTC+3
_FALLBACK_TZ: dict[str, timezone] = {
    "UTC": timezone.utc,
    "Europe/Moscow": timezone(timedelta(hours=3), name="Europe/Moscow"),
}

_DATE_DMY_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_DATE_YMD_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def stats_timezone() -> timezone:
    name = DEFAULT_TIMEZONE.strip() or "Europe/Moscow"
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return _FALLBACK_TZ.get(name, _FALLBACK_TZ["Europe/Moscow"])


def parse_count_from(date_text: str, time_text: str = "00:00") -> datetime:
    """Парсит дату/время в локальной зоне STATS_TIMEZONE, возвращает aware UTC."""
    date_clean = date_text.strip()
    time_clean = (time_text or "00:00").strip()

    parsed_date: date | None = None
    m = _DATE_DMY_RE.match(date_clean)
    if m:
        day, month, year = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        parsed_date = date(year, month, day)
    else:
        m = _DATE_YMD_RE.match(date_clean)
        if m:
            year, month, day = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            parsed_date = date(year, month, day)

    if parsed_date is None:
        raise ValueError(
            "Неверная дата. Форматы: ДД.ММ.ГГГГ (01.06.2025) или ГГГГ-ММ-ДД (2025-06-01)."
        )

    tm = _TIME_RE.match(time_clean)
    if tm is None:
        raise ValueError("Неверное время. Формат: ЧЧ:ММ (например 14:30).")

    hour, minute = int(tm.group(1)), int(tm.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("Время вне диапазона 00:00–23:59.")

    local_dt = datetime.combine(parsed_date, time(hour, minute), tzinfo=stats_timezone())
    return local_dt.astimezone(timezone.utc)


def format_count_from_display(utc_dt: datetime) -> str:
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    local = utc_dt.astimezone(stats_timezone())
    tz_name = DEFAULT_TIMEZONE
    return local.strftime(f"%d.%m.%Y %H:%M ({tz_name})")


def serialize_count_from(utc_dt: datetime) -> str:
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(timezone.utc).isoformat()


def deserialize_count_from(raw: str) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def message_counts_from(message_created_at: datetime, count_from_utc: Optional[datetime]) -> bool:
    if count_from_utc is None:
        return True
    created = message_created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created.astimezone(timezone.utc) >= count_from_utc

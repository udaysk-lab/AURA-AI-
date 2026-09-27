"""One place that knows what "today" and "5pm" mean for a particular user.

Everything is stored in UTC. The mistakes happen at the edges, where a phrase
like "tomorrow at 5pm", a day boundary, or a greeting has to be read in the
user's own clock. Before this module those were all computed in UTC, so for a
user in India (UTC+5:30):

  * "tomorrow 5pm" was saved as 17:00 UTC and showed up as 10:30 PM;
  * free slots were offered from 09:00 UTC, i.e. starting at 2:30 PM;
  * "today's meetings" ran from 05:30 to 05:30;
  * the dashboard said "Good afternoon" at half past ten at night.

A user's zone is an IANA name (``Asia/Kolkata``) held on ``User.timezone``. The
browser supplies it at sign-up and whenever the stored value is still the
``UTC`` default. Anything unknown or malformed falls back to UTC rather than
failing the request.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

# What ZoneInfo raises for a name that isn't a zone. OSError matters: a region
# like "America" or "Asia" is a directory in the zone database, so looking it up
# raises IsADirectoryError, which used to escape as a 500 from PATCH /me.
_BAD_ZONE = (ZoneInfoNotFoundError, ValueError, TypeError, OSError)


def zone(name: str | None) -> tzinfo:
    """The tzinfo for an IANA name, or UTC when it's missing or unknown."""
    if not name or name.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except _BAD_ZONE:
        return UTC


def is_valid_zone(name: str | None) -> bool:
    if not name or len(name) > 64:
        return False
    if name.upper() == "UTC":
        return True
    try:
        ZoneInfo(name)
    except _BAD_ZONE:
        return False
    return True


def user_zone(user) -> tzinfo:
    return zone(getattr(user, "timezone", None))


def aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on the way back out; stored values are UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def now_in(tz: tzinfo) -> datetime:
    return datetime.now(UTC).astimezone(tz)


def local_midnight(day: date, tz: tzinfo) -> datetime:
    """Midnight at the start of `day` in `tz`, as an aware datetime."""
    return datetime.combine(day, time.min, tzinfo=tz)


def day_bounds(
    tz: tzinfo = UTC,
    reference: datetime | None = None,
    day_offset: int = 0,
    days: int = 1,
) -> tuple[datetime, datetime]:
    """[start, end) of the local day(s), returned in UTC.

    Built from calendar dates rather than by adding 24h, so a day that loses or
    gains an hour to daylight saving is still exactly one local day.
    """
    local = (aware(reference) or datetime.now(UTC)).astimezone(tz)
    first = local.date() + timedelta(days=day_offset)
    start = local_midnight(first, tz)
    end = local_midnight(first + timedelta(days=days), tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def at_local(day: date, hour: int, minute: int, tz: tzinfo) -> datetime:
    """A wall-clock time on a local date, converted to UTC."""
    return datetime.combine(day, time(hour, minute), tzinfo=tz).astimezone(UTC)


def local_hour(dt: datetime, tz: tzinfo) -> int:
    return aware(dt).astimezone(tz).hour


def fmt_clock(dt: datetime | None, tz: tzinfo) -> str:
    """24-hour wall-clock time in the user's zone, e.g. 14:30."""
    if dt is None:
        return "—"
    return aware(dt).astimezone(tz).strftime("%H:%M")


def fmt_when(dt: datetime | None, tz: tzinfo) -> str:
    """Day and time in the user's zone, e.g. Mon 28 Sep, 14:30."""
    if dt is None:
        return "—"
    local = aware(dt).astimezone(tz)
    return f"{local.strftime('%a')} {local.day} {local.strftime('%b')}, {local.strftime('%H:%M')}"


def zone_label(tz: tzinfo) -> str:
    return getattr(tz, "key", None) or "UTC"

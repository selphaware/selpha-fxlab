"""The FX trading week and the intraday session map.

The single most important fact in this module: **the FX week boundary is not a
fixed UTC hour.** It tracks 17:00 ``America/New_York``, so it sits at 21:00 UTC
during northern summer and 22:00 UTC during northern winter. Measured against
the live feed (see SPEC.md): EURUSD Friday 2026-07-17 20:00Z carries 1,163
ticks and 21:00Z is empty, while Friday 2026-01-09 21:00Z still carries 868
ticks. Hardcoding 21:00 UTC is wrong for roughly half of every year and fails
silently, corrupting every session and spread statistic downstream.

Everything here therefore derives the boundary with ``zoneinfo``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Final
from zoneinfo import ZoneInfo

#: The exchange whose local clock defines the FX week.
FX_WEEK_TZ: Final[ZoneInfo] = ZoneInfo("America/New_York")

#: Local hour at which the week opens (Sunday) and closes (Friday).
WEEK_OPEN_HOUR: Final[int] = 17
WEEK_CLOSE_HOUR: Final[int] = 17

_MONDAY: Final[int] = 0
_FRIDAY: Final[int] = 4
_SATURDAY: Final[int] = 5
_SUNDAY: Final[int] = 6

#: Regional session windows, expressed in each centre's own local clock so the
#: map stays correct across every DST transition.
_SESSION_WINDOWS: Final[tuple[tuple[str, str, int, int], ...]] = (
    ("london", "Europe/London", 8, 16),
    ("new_york", "America/New_York", 8, 17),
    ("tokyo", "Asia/Tokyo", 9, 18),
)
_SESSION_TZ: Final[dict[str, ZoneInfo]] = {
    name: ZoneInfo(tz) for name, tz, _o, _c in _SESSION_WINDOWS
}

#: Session label used outside every window above.
SESSION_SYDNEY: Final[str] = "sydney"
#: Session label for the London/New York overlap, the deepest liquidity of the day.
SESSION_OVERLAP: Final[str] = "london_ny_overlap"

#: Every label :func:`session_of` can return.
SESSIONS: Final[tuple[str, ...]] = (
    "tokyo", "london", SESSION_OVERLAP, "new_york", SESSION_SYDNEY,
)


def _require_utc(ts: dt.datetime) -> dt.datetime:
    """Return ``ts`` as an aware UTC datetime, rejecting naive input."""
    if ts.tzinfo is None:
        raise ValueError(
            f"{ts!r} is naive; every timestamp in fxlab is tz-aware UTC "
            "(a naive timestamp silently shifts at every session boundary)")
    return ts.astimezone(dt.timezone.utc)


def is_market_open(ts: dt.datetime) -> bool:
    """True when ``ts`` falls inside the FX trading week.

    Args:
        ts: A tz-aware timestamp.

    Returns:
        Whether the market is open. The week runs from Sunday 17:00 to Friday
        17:00 ``America/New_York``; Saturday is always closed.
    """
    local = _require_utc(ts).astimezone(FX_WEEK_TZ)
    weekday = local.weekday()
    if weekday == _SATURDAY:
        return False
    if weekday == _SUNDAY:
        return local.hour >= WEEK_OPEN_HOUR
    if weekday == _FRIDAY:
        return local.hour < WEEK_CLOSE_HOUR
    return True


def week_open_before(ts: dt.datetime) -> dt.datetime:
    """Return the UTC instant of the most recent weekly open at or before ``ts``."""
    local = _require_utc(ts).astimezone(FX_WEEK_TZ)
    candidate = local.replace(hour=WEEK_OPEN_HOUR, minute=0, second=0, microsecond=0)
    candidate -= dt.timedelta(days=(local.weekday() + 1) % 7)
    if candidate > local:
        candidate -= dt.timedelta(days=7)
    return candidate.astimezone(dt.timezone.utc)


def week_bounds(ts: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """Return ``(open, close)`` in UTC for the FX week containing ``ts``.

    For a timestamp inside the weekend gap this returns the week that has just
    ended, which is what a coverage report wants: the gap belongs to the week
    before it.
    """
    open_utc = week_open_before(ts)
    local_open = open_utc.astimezone(FX_WEEK_TZ)
    local_close = (local_open + dt.timedelta(days=5)).replace(
        hour=WEEK_CLOSE_HOUR, minute=0, second=0, microsecond=0)
    return open_utc, local_close.astimezone(dt.timezone.utc)


def session_of(ts: dt.datetime) -> str:
    """Classify ``ts`` into one intraday session label.

    Args:
        ts: A tz-aware timestamp.

    Returns:
        One of :data:`SESSIONS`. Windows are evaluated in each centre's own
        local time, so the boundaries move with British Summer Time and US
        daylight saving independently, as they do in reality.
    """
    utc = _require_utc(ts)
    active = set()
    for name, _tz, open_h, close_h in _SESSION_WINDOWS:
        local = utc.astimezone(_SESSION_TZ[name])
        if local.weekday() > _FRIDAY:
            continue
        if open_h <= local.hour < close_h:
            active.add(name)
    if "london" in active and "new_york" in active:
        return SESSION_OVERLAP
    for name in ("london", "new_york", "tokyo"):
        if name in active:
            return name
    return SESSION_SYDNEY


def _as_utc_index(values: Any) -> Any:
    """Coerce a datetime-like container to a tz-aware UTC ``DatetimeIndex``."""
    import pandas as pd

    index = pd.DatetimeIndex(values)
    if index.tz is None:
        raise ValueError(
            "timestamps are tz-naive; every timestamp in fxlab is tz-aware UTC")
    return index.tz_convert("UTC")


def market_open_mask(values: Any) -> Any:
    """Vectorised :func:`is_market_open` over a datetime-like container.

    Args:
        values: Anything ``pandas.DatetimeIndex`` accepts, tz-aware.

    Returns:
        A boolean numpy array, ``True`` where the market is open.
    """
    import numpy as np

    local = _as_utc_index(values).tz_convert(FX_WEEK_TZ)
    weekday = local.weekday.to_numpy()
    hour = local.hour.to_numpy()
    closed = (
        (weekday == _SATURDAY)
        | ((weekday == _SUNDAY) & (hour < WEEK_OPEN_HOUR))
        | ((weekday == _FRIDAY) & (hour >= WEEK_CLOSE_HOUR))
    )
    return np.asarray(~closed)


def session_labels(values: Any) -> Any:
    """Vectorised :func:`session_of` over a datetime-like container.

    Args:
        values: Anything ``pandas.DatetimeIndex`` accepts, tz-aware.

    Returns:
        A numpy object array of session labels, one per input timestamp.
    """
    import numpy as np

    index = _as_utc_index(values)
    active: dict[str, Any] = {}
    for name, _tz, open_h, close_h in _SESSION_WINDOWS:
        local = index.tz_convert(_SESSION_TZ[name])
        hour = local.hour.to_numpy()
        weekday = local.weekday.to_numpy()
        active[name] = (weekday <= _FRIDAY) & (hour >= open_h) & (hour < close_h)

    labels = np.full(len(index), SESSION_SYDNEY, dtype=object)
    labels[active["tokyo"]] = "tokyo"
    labels[active["new_york"]] = "new_york"
    labels[active["london"]] = "london"
    labels[active["london"] & active["new_york"]] = SESSION_OVERLAP
    return labels


def market_open_mask_micros(ts_us: Any) -> Any:
    """Vectorised market-open test over int64 epoch microseconds.

    Args:
        ts_us: Integer microseconds since the Unix epoch, UTC.

    Returns:
        A boolean numpy array, ``True`` where the market is open.

    The tick columns carry integer microseconds rather than datetimes, and a
    bare ``datetime64`` array is tz-naive; this wrapper attaches the UTC zone
    the integers already imply instead of letting a naive value through.
    """
    import numpy as np
    import pandas as pd

    values = np.asarray(ts_us, dtype="int64").astype("datetime64[us]")
    return market_open_mask(pd.DatetimeIndex(values, tz="UTC"))


def session_labels_micros(ts_us: Any) -> Any:
    """Vectorised :func:`session_of` over int64 epoch microseconds."""
    import numpy as np
    import pandas as pd

    values = np.asarray(ts_us, dtype="int64").astype("datetime64[us]")
    return session_labels(pd.DatetimeIndex(values, tz="UTC"))

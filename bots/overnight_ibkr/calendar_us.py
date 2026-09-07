"""US equity market calendar: holidays, early closes, session times.

Rule-based (no hard-coded year lists, no expiry): observed-holiday logic
plus computed Easter for Good Friday. Covers NYSE/Nasdaq full-day
holidays and the standard 13:00 ET early closes.

This is the bot's *offline* gate. When live, the broker adapter also
reads today's actual liquid hours from IBKR contract details, which wins
over this module (it catches unscheduled closures, e.g. days of
mourning). Stdlib only.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)
OPEN_TIME = time(9, 30)

# Nasdaq cutoffs relative to the close: MOC entry closes 5 min before the
# cross (15:55 regular, 12:55 early); we target a wider safety margin.
MOC_CUTOFF_BEFORE_CLOSE = timedelta(minutes=5)
# MOO entry closes at 09:28 for the 09:30 cross.
MOO_CUTOFF = time(9, 28)


def _easter(year: int) -> date:
    """Anonymous Gregorian computus."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th (1-based) given weekday (Mon=0) of a month."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date | None:
    """Exchange observation of a fixed-date holiday.

    Saturday -> preceding Friday; Sunday -> following Monday. Exception:
    when New Year's Day falls on Saturday the exchanges do NOT observe it
    on the prior Friday (that Friday belongs to the old year and stays a
    full trading day - e.g. 2021-12-31); there is simply no observance.
    """
    if d.weekday() == 5:
        return None if (d.month == 1 and d.day == 1) else d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def market_holidays(year: int) -> set[date]:
    hs: set[date] = set()
    for fixed in (date(year, 1, 1), date(year, 6, 19), date(year, 7, 4),
                  date(year, 12, 25)):
        obs = _observed(fixed)
        if obs is not None:
            hs.add(obs)
    hs.add(_nth_weekday(year, 1, 0, 3))    # MLK Day
    hs.add(_nth_weekday(year, 2, 0, 3))    # Washington's Birthday
    hs.add(_easter(year) - timedelta(days=2))  # Good Friday
    hs.add(_last_weekday(year, 5, 0))      # Memorial Day
    hs.add(_nth_weekday(year, 9, 0, 1))    # Labor Day
    hs.add(_nth_weekday(year, 11, 3, 4))   # Thanksgiving
    return hs


def early_closes(year: int) -> set[date]:
    """13:00 ET early-close days."""
    ec: set[date] = set()
    hols = market_holidays(year)
    # July 3: early close when it's a weekday and not itself the observed
    # July-4th holiday (i.e. July 4 is not a Saturday).
    j3 = date(year, 7, 3)
    if j3.weekday() < 5 and j3 not in hols:
        ec.add(j3)
    # Day after Thanksgiving.
    ec.add(_nth_weekday(year, 11, 3, 4) + timedelta(days=1))
    # Christmas Eve: weekday and not the observed Christmas holiday.
    c24 = date(year, 12, 24)
    if c24.weekday() < 5 and c24 not in hols:
        ec.add(c24)
    return ec


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in market_holidays(d.year)


def close_time(d: date) -> time:
    return EARLY_CLOSE if d in early_closes(d.year) else REGULAR_CLOSE


def close_dt(d: date) -> datetime:
    return datetime.combine(d, close_time(d), tzinfo=ET)


def moc_deadline(d: date) -> datetime:
    """Last moment we allow ourselves to submit the MOC (5 min of margin
    on top of Nasdaq's own 5-minute cutoff)."""
    return close_dt(d) - MOC_CUTOFF_BEFORE_CLOSE - timedelta(minutes=5)


def next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    while not is_trading_day(n):
        n += timedelta(days=1)
    return n


def now_et() -> datetime:
    return datetime.now(tz=ET)

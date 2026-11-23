"""The town's calendar. Day 0 is a Monday kept with each place (`Store.start`), Monday 2 March
2026 unless the place was made with another; the calendar in use is that of the store made or
loaded last (`use`)."""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import date, timedelta

DEFAULT_START = date(2026, 3, 2)
_START = [DEFAULT_START]
DAYS: tuple[str, ...] = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS: tuple[str, ...] = ("January", "February", "March", "April", "May", "June", "July",
                           "August", "September", "October", "November", "December")


_FILLER = ("the", "of")
_ORDINALS = ("st", "nd", "rd", "th")
_WEEKDAY_WORDS = {w: i for i, name in enumerate(DAYS) for w in (name.lower(), name[:3].lower())}
_MONTH_WORDS = {w: i + 1 for i, name in enumerate(MONTHS) for w in (name.lower(), name[:3].lower())} | {"sept": 9}


def _drop_ordinal(word: str) -> str:
    """'12th' -> '12'; any other word unchanged."""
    if word[-2:] in _ORDINALS and word[:-2].isdigit():
        return word[:-2]
    return word


def use(start: "date | str") -> None:
    """Make `start` (a Monday, or its ISO text) day 0 of the calendar in use."""
    d = date.fromisoformat(start) if isinstance(start, str) else start
    if d.weekday() != 0:
        raise ValueError(f"day 0 must be a Monday, not {d.isoformat()}")
    _START[0] = d


def start() -> date:
    """Day 0 of the calendar in use."""
    return _START[0]


def parse_start(text: object) -> date:
    """A start date as typed (`2026-11-30`): an ISO date that is a Monday, or ValueError."""
    try:
        d = date.fromisoformat(text.strip()) if isinstance(text, str) and _ISO.fullmatch(text.strip()) else None
    except ValueError:
        d = None
    if d is None:
        raise ValueError(f"the start must be a date written YYYY-MM-DD, got {text!r}")
    if d.weekday() != 0:
        raise ValueError(f"the start must be a Monday; {d.isoformat()} is a {DAYS[d.weekday()]}")
    return d


def on(day: int) -> date:
    """The date of `day`."""
    return _START[0] + timedelta(days=day)


def iso(day: int) -> str:
    """`day` as `2026-03-09`."""
    return on(day).isoformat()


def season_week(day: int) -> int:
    """The week number food's seasons are read at: `week(day)` for a place starting on the
    default Monday, moved by the weeks its own start lies after that one, so that the seasons
    follow the calendar."""
    return week(day) + (_START[0] - DEFAULT_START).days // 7


def dow(day: int) -> int:
    """Day of the week, 0 for Monday."""
    return day % 7


def week(day: int) -> int:
    """Week number, starting at 1."""
    return day // 7 + 1


def weekday(word: object) -> int | None:
    """The day of the week a word names, 0 for Monday: a full name or its first three
    letters, in any case, with surrounding spaces and a full stop allowed. Never raises."""
    if not isinstance(word, str):
        return None
    return _WEEKDAY_WORDS.get(word.strip().lower().removesuffix("."))


_YEARS = [False]


@contextmanager
def with_years():
    """Within it every day `date_text` writes carries its year, taken from the day itself: for a text
    built from the records for a reader who is shown each date with its year (an assistant)."""
    before = _YEARS[0]
    _YEARS[0] = True
    try:
        yield
    finally:
        _YEARS[0] = before


def _bare(day: int) -> str:
    d = on(day)
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def date_text(day: int) -> str:
    """A day as written: `Friday 27 March` (with its year within `with_years`)."""
    return f"{_bare(day)} {on(day).year}" if _YEARS[0] else _bare(day)


def date_year(day: int) -> str:
    """A day as written with its year always: `Friday 27 March 2026`."""
    return f"{_bare(day)} {on(day).year}"


def date_in(day: int, today: int) -> str:
    """A day as written, with its year when that is not the year of `today` (always within `with_years`)."""
    year = on(day).year
    return _bare(day) + ("" if year == on(today).year and not _YEARS[0] else f" {year}")


def nearest_year(weekday: int | None, day: int, month: int, today: int) -> int | None:
    """The year in which `day` `month` (on `weekday`, when one is given) lies nearest the day `today`: this
    year's, or the year before or after, whichever is closest; None when none of the three is such a day."""
    ref = on(today)
    found = []
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if weekday is None or d.weekday() == weekday:
            found.append((abs((d - ref).days), y))
    return min(found)[1] if found else None


DATE_FORM = "Write it like Monday 16 March."
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def unreadable(value: object) -> str:
    """The refusal for a date that cannot be read, with the form one is written in."""
    from townsim.town.acts import shown           # acts reads the calendar
    return f"'{shown(value)}' is not a date that can be read. {DATE_FORM}"


def parse_near(text: str, today: int) -> int | None:
    """The day a written date names, before or after `today`: an ISO date, or a day and month in the year written
    after it, or with no year the such date nearest `today` (this year's or the year either side; with the day of
    the week written beside it, the one that falls on it when one does). `today`, `tomorrow` and a bare weekday read
    as `parse_date` reads them. None for what is no date. Never raises."""
    if not isinstance(text, str):
        return None
    plain = text.strip().lower().rstrip(".")
    iso = _ISO.fullmatch(plain)
    if iso:
        try:
            return (date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))) - _START[0]).days
        except ValueError:
            return None
    words = [_drop_ordinal(w.rstrip(".")) for w in text.strip().lower().replace(",", " ").split()
             if w.rstrip(".") not in _FILLER]
    months = [_MONTH_WORDS[w] for w in words if w in _MONTH_WORDS]
    numbers = [int(w) for w in words if w.isascii() and w.isdigit() and len(w) <= 2]
    if len(months) != 1 or len(numbers) != 1:
        return parse_date(text, today)
    year = int(words[-1]) if words and len(words[-1]) == 4 and words[-1].isascii() and words[-1].isdigit() else None
    weekdays = [_WEEKDAY_WORDS[w] for w in words if w in _WEEKDAY_WORDS]
    if len(weekdays) + len(months) + len(numbers) + (year is not None) != len(words) or len(weekdays) > 1:
        return None
    if year is None:
        year = nearest_year(weekdays[0] if weekdays else None, numbers[0], months[0], today)
        if year is None and weekdays:
            year = nearest_year(None, numbers[0], months[0], today)
    if year is None or not 2000 <= year <= 2200:
        return None
    try:
        return (date(year, months[0], numbers[0]) - _START[0]).days
    except ValueError:
        return None


def parse_date(text: str, today: int, passed: bool = False) -> int | None:
    """The day a written date names, or None if it names none on or after `today`.

    `today` and `tomorrow` are those days; an ISO date (`2026-03-16`) is that day. A date
    written with slashes is never read, being ambiguous. A bare weekday is the next such day
    strictly after `today`. A day and month, in either order, is that date in the year written
    after it, or with no year the next such date on or after `today` (so "4 January" said in
    November is next January), unless the day of the week written beside it is that of this
    year's, which has passed; otherwise the date wins over any weekday beside it. Commas,
    ordinals ("12th"), the words "the" and "of", three-letter weekdays and months ("Thu",
    "Mar", and "Sept") and full stops after words are allowed. With `passed`, a day and month whose day of the
    week written is not that of the coming such date but is that of the one before it, which has passed, names
    none ("Monday 28 December" said in January). Never raises.
    """
    if not isinstance(text, str):
        return None
    plain = text.strip().lower().rstrip(".")
    if plain in ("today", "tomorrow"):
        return today + (plain == "tomorrow")
    iso = _ISO.fullmatch(plain)
    if iso:
        try:
            day = (date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))) - _START[0]).days
        except ValueError:
            return None
        return day if day >= today else None
    words = [_drop_ordinal(w.rstrip(".")) for w in text.strip().lower().replace(",", " ").split()
             if w.rstrip(".") not in _FILLER]
    year = None
    if words and len(words[-1]) == 4 and words[-1].isascii() and words[-1].isdigit():
        year = int(words.pop())
        if not 2000 <= year <= 2200:
            return None
    weekdays = [_WEEKDAY_WORDS[w] for w in words if w in _WEEKDAY_WORDS]
    months = [_MONTH_WORDS[w] for w in words if w in _MONTH_WORDS]
    numbers = [int(w) for w in words if w.isascii() and w.isdigit() and len(w) <= 2]
    if len(weekdays) + len(months) + len(numbers) != len(words) or len(weekdays) > 1:
        return None
    if year is not None and (not months or not numbers):
        return None
    if not months and not numbers:
        if not weekdays:
            return None
        return today + (weekdays[0] - dow(today) - 1) % 7 + 1
    if len(months) != 1 or len(numbers) != 1:
        return None

    def day_in(y: int) -> int | None:
        try:
            return (date(y, months[0], numbers[0]) - _START[0]).days
        except ValueError:
            return None
    if year is not None:
        day = day_in(year)
        return day if day is not None and day >= today else None
    this = on(max(today, 0)).year

    def last_years(found: int, y: int) -> bool:
        """Whether the weekday written is not `found`'s but is that of the year before's, which has passed."""
        if not (passed and weekdays and dow(found) != weekdays[0]):
            return False
        before = day_in(y - 1)
        return before is not None and before < today and dow(before) == weekdays[0]
    day = day_in(this)
    if day is not None and day >= today:
        return None if last_years(day, this) else day
    if day is not None and weekdays and dow(day) == weekdays[0]:
        return None                       # the day of the week written is this year's: it has passed
    later = day_in(this + 1)
    if later is not None and later >= today and last_years(later, this + 1):
        return None
    return later if later is not None and later >= today else None

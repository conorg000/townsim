"""The town's calendar. Day 0 is Monday 2 March 2026; every date is in 2026."""

from __future__ import annotations

import re
from datetime import date, timedelta

DAY_ZERO = date(2026, 3, 2)
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


def date_text(day: int) -> str:
    d = DAY_ZERO + timedelta(days=day)
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def date_year(day: int) -> str:
    """A day as written with its year always: `Friday 27 March 2026`."""
    return f"{date_text(day)} {(DAY_ZERO + timedelta(days=day)).year}"


def date_in(day: int, today: int) -> str:
    """A day as written, with its year when that is not the year of `today`."""
    year = (DAY_ZERO + timedelta(days=day)).year
    return date_text(day) + ("" if year == (DAY_ZERO + timedelta(days=today)).year else f" {year}")


DATE_FORM = "Write it like Monday 16 March."
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def unreadable(value: object) -> str:
    """The refusal for a date that cannot be read, with the form one is written in."""
    from townsim.town.acts import shown           # acts reads the calendar
    return f"'{shown(value)}' is not a date that can be read. {DATE_FORM}"


def parse_date(text: str, today: int) -> int | None:
    """The day a written date names, or None if it names none on or after `today`.

    `today` and `tomorrow` are those days; an ISO date (`2026-03-16`) is that day. A date
    written with slashes is never read, being ambiguous. A bare weekday is the next such day
    strictly after `today`. A day and month, in either order, is that date in the year written
    after it, or with no year the next such date on or after `today` (so "4 January" said in
    November is next January), unless the day of the week written beside it is that of this
    year's, which has passed; otherwise the date wins over any weekday beside it. Commas,
    ordinals ("12th"), the words "the" and "of", three-letter weekdays and months ("Thu",
    "Mar", and "Sept") and full stops after words are allowed. Never raises.
    """
    if not isinstance(text, str):
        return None
    plain = text.strip().lower().rstrip(".")
    if plain in ("today", "tomorrow"):
        return today + (plain == "tomorrow")
    iso = _ISO.fullmatch(plain)
    if iso:
        try:
            day = (date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))) - DAY_ZERO).days
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
            return (date(y, months[0], numbers[0]) - DAY_ZERO).days
        except ValueError:
            return None
    if year is not None:
        day = day_in(year)
        return day if day is not None and day >= today else None
    this = (DAY_ZERO + timedelta(days=max(today, 0))).year
    day = day_in(this)
    if day is not None and day >= today:
        return day
    if day is not None and weekdays and dow(day) == weekdays[0]:
        return None                       # the day of the week written is this year's: it has passed
    later = day_in(this + 1)
    return later if later is not None and later >= today else None

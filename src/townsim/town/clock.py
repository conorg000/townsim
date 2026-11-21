"""The town's calendar. Day 0 is Monday 2 March 2026; every date is in 2026."""

from __future__ import annotations

from datetime import date, timedelta

DAY_ZERO = date(2026, 3, 2)
DAYS: tuple[str, ...] = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS: tuple[str, ...] = ("January", "February", "March", "April", "May", "June", "July",
                           "August", "September", "October", "November", "December")


_FILLER = ("the", "of")
_ORDINALS = ("st", "nd", "rd", "th")
_YEAR = "2026"
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


def parse_date(text: str, today: int) -> int | None:
    """The day a written date names, or None if it names none on or after `today`.

    A bare weekday is the next such day strictly after `today`. A day and month is that
    date in 2026, in either order, and wins over any weekday written beside it. Commas,
    ordinals ("12th"), the words "the" and "of", three-letter weekdays and months ("Thu",
    "Mar", and "Sept"), full stops after words, and a trailing year 2026 after a day and
    month are allowed. Never raises.
    """
    if not isinstance(text, str):
        return None
    words = [_drop_ordinal(w.rstrip(".")) for w in text.strip().lower().replace(",", " ").split()
             if w.rstrip(".") not in _FILLER]
    year = bool(words) and words[-1] == _YEAR
    if year:
        words.pop()
    weekdays = [_WEEKDAY_WORDS[w] for w in words if w in _WEEKDAY_WORDS]
    months = [_MONTH_WORDS[w] for w in words if w in _MONTH_WORDS]
    numbers = [int(w) for w in words if w.isascii() and w.isdigit() and len(w) <= 2]
    if len(weekdays) + len(months) + len(numbers) != len(words) or len(weekdays) > 1:
        return None
    if year and (not months or not numbers):
        return None
    if not months and not numbers:
        if not weekdays:
            return None
        return today + (weekdays[0] - dow(today) - 1) % 7 + 1
    if len(months) != 1 or len(numbers) != 1:
        return None
    try:
        day = (date(2026, months[0], numbers[0]) - DAY_ZERO).days
    except ValueError:
        return None
    return day if day >= today else None

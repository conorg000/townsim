"""The town's calendar. Day 0 is Monday 2 March 2026; every date is in 2026."""

from __future__ import annotations

from datetime import date, timedelta

DAY_ZERO = date(2026, 3, 2)
DAYS: tuple[str, ...] = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS: tuple[str, ...] = ("January", "February", "March", "April", "May", "June", "July",
                           "August", "September", "October", "November", "December")


_FILLER = ("the", "of")
_ORDINALS = ("st", "nd", "rd", "th")


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


def date_text(day: int) -> str:
    d = DAY_ZERO + timedelta(days=day)
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def parse_date(text: str, today: int) -> int | None:
    """The day a written date names, or None if it names none on or after `today`.

    A bare weekday is the next such day strictly after `today`. A day and month is that
    date in 2026, in either order, and wins over any weekday written beside it. Commas,
    ordinals ("12th") and the words "the" and "of" are allowed. Never raises.
    """
    if not isinstance(text, str):
        return None
    words = [_drop_ordinal(w) for w in text.strip().rstrip(".").lower().replace(",", " ").split()
             if w not in _FILLER]
    weekdays = [i for i, name in enumerate(DAYS) if name.lower() in words]
    months = [i + 1 for i, name in enumerate(MONTHS) if name.lower() in words]
    numbers = [int(w) for w in words if w.isascii() and w.isdigit() and len(w) <= 2]
    if len(weekdays) + len(months) + len(numbers) != len(words) or len(weekdays) > 1:
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

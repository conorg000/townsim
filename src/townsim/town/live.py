"""A live run of a generated hamlet, its people played by language models, kept in a `RunDB`.

`play` makes the run (or resumes it) and runs it week by week behind a spending cap. The
run's spend is what is stored: the sum of its stored replies' costs plus what was lost to
attempts that raised (kept in the run's note `lost_spend`), so a resumed run counts what it
spent before. A resumed run is played by the models it began with: if the families file now
maps a family to another model, or the seed differs, the resume is refused before anything is
asked. Stored replies are served as stored, never asked again.

The completion function is an argument (LiteLLM's `completion` in live use); nothing here
imports a provider's client or reads a key.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from townsim.estimate import Price
from townsim.town import gen
from townsim.town.clock import date_text
from townsim.town.families import Family
from townsim.town.hamlet import RUN_WEEKS
from townsim.town.modelreplier import ModelReplier, safe
from townsim.town.replies import is_instance_id
from townsim.town.rundb import RunDB
from townsim.town.runs import resume, run

MAX_CAP = 5.0   # dollars a live run may be capped at, at most


@dataclass(frozen=True)
class LiveResult:
    days_done: int
    spent: float
    stopped: str | None
    engine_errors: int


def lost_spend(db: RunDB, run_id: str) -> float:
    """Dollars lost to attempts that raised, as kept in the run's note."""
    return float(db.note(run_id, "lost_spend") or 0)


def spent(db: RunDB, run_id: str) -> float:
    """The run's spend: its stored replies' costs plus what was lost."""
    return db.spent(run_id) + lost_spend(db, run_id)


def _noted(families: dict[str, Family]) -> dict[str, dict]:
    """The families as noted with a run: name -> {model, params}."""
    return {name: {"model": f.model, "params": dict(f.params)} for name, f in families.items()}


def _check_resume(db: RunDB, run_id: str, seed: int, families: dict[str, Family], weeks: int) -> None:
    """Raise ValueError if the run was made with another seed, other families (a family
    added, removed or renamed, another model, or another parameter) or a deck too short for
    `weeks`."""
    noted_seed = db.note(run_id, "seed")
    if noted_seed is None or noted_seed != str(seed):
        raise ValueError(f"run {run_id!r} was made with seed {noted_seed}, not {seed}")
    noted = json.loads(db.note(run_id, "families") or "{}")
    now = _noted(families)
    for name in [*noted, *[n for n in now if n not in noted]]:
        was, is_ = noted.get(name), now.get(name)
        if was is None or is_ is None or was["model"] != is_["model"]:
            raise ValueError(f"run {run_id!r} was played with family {name!r} as "
                             f"{was['model'] if was else 'absent'}; the families file now has it as "
                             f"{is_['model'] if is_ else 'absent'}")
        for param in sorted(set(was["params"]) | set(is_["params"])):
            if was["params"].get(param) != is_["params"].get(param):
                raise ValueError(f"run {run_id!r} was played with family {name!r} with {param} "
                                 f"{was['params'].get(param, 'unset')}; the families file now has "
                                 f"{is_['params'].get(param, 'unset')}")
    deck = int(db.note(run_id, "deck_weeks") or RUN_WEEKS)
    if weeks > deck:
        raise ValueError(f"run {run_id!r} has dated events for {deck} weeks only, not {weeks}")


def refusal(db: RunDB, run_id: str) -> str | None:
    """A line naming the first stored refusal of the run (its day, person and model), or None."""
    for key, reply in db.replies(run_id):
        if isinstance(reply.error, str) and reply.error.startswith("refused:"):
            _, model, day, actor = key.split("|")[:4]
            return f"run {run_id!r} was refused on day {day[1:]}: {actor} played by {model}"
    return None


@contextmanager
def _only_process(db: RunDB, run_id: str) -> Iterator[None]:
    """Hold an exclusive lock on `<db path>.lock` for the duration, or raise ValueError at
    once if another process holds it. A database in memory needs none."""
    path = getattr(db, "path", ":memory:")
    if not path or path == ":memory:":
        yield
        return
    fd = os.open(f"{path}.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f"another process is running {run_id} in {os.path.basename(path)}") from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _week_counts(db: RunDB, run_id: str, first: int, last: int) -> tuple[int, int]:
    """Replies stored for days `first` to `last` (inclusive), and turns on those days whose
    reply could not be read."""
    days = {f"d{d}" for d in range(first, last + 1)}
    asks = sum(1 for _, slot in db.reply_entries(run_id) if slot.split("|")[1] in days)
    kept = set(db.turn_days(run_id))
    unread = sum(1 for d in range(first, last + 1) if d in kept for t in db.turns(run_id, d) if t.invalid)
    return asks, unread


def _check_seat(db: RunDB, run_id: str, store, seat: Family | None) -> None:
    """Raise ValueError if the run was played with another seat than `seat`, or has a launched
    product (whose instances call the seat) and no seat is given."""
    noted = json.loads(db.note(run_id, "seat") or "null")
    now = {"model": seat.model, "params": dict(seat.params)} if seat is not None else None
    if noted is not None and noted != now:
        raise ValueError(f"run {run_id!r} was played with the seat as {noted['model']} {noted['params']}; "
                         f"now it is {now['model'] + ' ' + str(now['params']) if now else 'not given'}")
    if seat is None and store.all("products"):
        raise ValueError(f"run {run_id!r} has the product launched, and its instances need a seat (--seat)")


def play(db: RunDB, run_id: str, seed: int, weeks: int, families: dict[str, Family], prices: dict[str, Price],
         cap: float, completion_fn: Callable, cost_fn: Callable | None = None, echo: Callable[[str], None] = print,
         sleep_fn: Callable[[float], None] | None = None, past_refusal: bool = False,
         seat: Family | None = None) -> LiveResult:
    """Make the run `run_id` (or resume it) and run it to `weeks` weeks from day 0, echoing a
    line a week; stop when the replier stops, echoing why.

    Raises ValueError, before anything is asked, for a cap that is not above 0 and at most
    `MAX_CAP`, weeks below 1, a resume whose noted seed or families differ, a run held by
    another process (a lock on `<db path>.lock` is held for the whole call), or a resume of a
    run holding a stored refusal, unless `past_refusal`, when that is echoed as a warning; or for
    a run played with another seat, or with the product launched and no `seat`. The seat's calls
    are made by the same replier, behind the same cap."""
    if not (isinstance(cap, (int, float)) and 0 < cap <= MAX_CAP):
        raise ValueError(f"the cap must be above $0 and at most ${MAX_CAP:g}, got {cap!r}")
    if weeks < 1:
        raise ValueError(f"weeks must be 1 or more, got {weeks}")
    with _only_process(db, run_id):
        return _play(db, run_id, seed, weeks, families, prices, cap, completion_fn, cost_fn, echo, sleep_fn,
                     past_refusal, seat)


def _play(db, run_id, seed, weeks, families, prices, cap, completion_fn, cost_fn, echo, sleep_fn,
          past_refusal, seat=None) -> LiveResult:
    try:
        db.get_run(run_id)
    except KeyError:
        deck = max(weeks, RUN_WEEKS)
        store = gen.generate(seed, list(families), weeks=deck)
        db.create_run(run_id, seed=seed)
        db.put_note(run_id, "families", json.dumps(_noted(families), sort_keys=True))
        db.put_note(run_id, "seed", str(seed))
        db.put_note(run_id, "deck_weeks", str(deck))
    else:
        _check_resume(db, run_id, seed, families, weeks)
        refused = refusal(db, run_id)
        if refused is not None:
            if not past_refusal:
                raise ValueError(f"{refused}; to go on past it, resume with --past-refusal")
            echo(f"warning: {refused}; going on past it")
        store = resume(db, run_id)
    _check_seat(db, run_id, store, seat)
    if seat is not None:
        db.put_note(run_id, "seat", json.dumps({"model": seat.model, "params": dict(seat.params)}, sort_keys=True))
    db.put_note(run_id, "cap", repr(float(cap)))

    def lost(amount: float) -> None:
        db.put_note(run_id, "lost_spend", repr(lost_spend(db, run_id) + float(amount)))

    family_of = {p.id: p.family for p in store.all("people")}
    extra = {} if sleep_fn is None else {"sleep_fn": sleep_fn}
    replier = ModelReplier(families, prices, family_of, completion_fn, cap=cap, spent=lambda: spent(db, run_id),
                           cost_fn=cost_fn, lost=lost, seat=seat, **extra)
    end = 7 * weeks
    stopped = None
    while store.day < end:
        start = store.day
        monday = start - start % 7
        days = min(monday + 7, end) - start
        store = run(db, run_id, store, replier, days)
        if store.day > start:
            asks, unread = _week_counts(db, run_id, monday, store.day - 1)
            echo(f"week {monday // 7 + 1}  {date_text(monday)}  {store.hash()[:12]}  asks {asks}  "
                 f"unreadable {unread}  spent ${spent(db, run_id):.4f}")
        if store.day < start + days:
            stopped = safe(replier.stopped or "stopped")
            echo(f"stopped on day {store.day}: {stopped}")
            break
    return LiveResult(store.day, spent(db, run_id), stopped, len(db.errors(run_id)))


def spend_by_family(db: RunDB, run_id: str) -> list[tuple[str, int, float]]:
    """(family, replies stored, dollars) for each family the run was played with, in the
    order noted; a stored reply's person is read from its key, and their family from the
    run's first snapshot."""
    noted = json.loads(db.note(run_id, "families") or "{}")
    days = db.snapshot_days(run_id)
    family_of = {p.id: p.family for p in db.load_snapshot(run_id, days[0]).all("people")} if days else {}
    counts = {name: [0, 0.0] for name in noted}
    for key, reply in db.replies(run_id):
        actor = key.split("|")[3]
        name = "seat" if is_instance_id(actor) else family_of.get(actor)
        counts.setdefault(name or "?", [0, 0.0])
        counts[name or "?"][0] += 1
        counts[name or "?"][1] += reply.cost
    return [(name, n, cost) for name, (n, cost) in counts.items()]

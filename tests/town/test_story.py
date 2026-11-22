"""`town-story`: a kept run read back as a plain account, from what is stored alone."""

import functools
import json
import re
import tempfile
from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner

from townsim.cli import app
from townsim.town import gen, hamlet_script, story
from townsim.town.ops_core import NOT_COUNTED
from townsim.town.replies import Reply
from townsim.town.rundb import RunDB
from townsim.town.runs import run

FAMILIES = ["a", "b", "c", "d"]
DAYS = 56
LETTER = "Dear you, I saw Mr Pettigrew.\n# Heading\n| a | b |\n|---|---|\n<b>bold</b> [link](http://x.test)\nyours"
LONG_THOUGHT = " ".join(f"w{i}" for i in range(90))


class _Injecting:
    """The hamlet's script, but on day 1 the first person asked who has someone at home writes
    them `LETTER`, sets up a standing payment to whoever their rent goes to and thinks
    `LONG_THOUGHT`; and on day 2 the first person asked answers with
    words no one can read."""

    def __init__(self, store):
        self.store = store
        self.letter_by = None
        self.unreadable_by = None

    def model_for(self, actor):
        return "scripted"

    def ask(self, key, messages):
        if key.purpose == "turn" and key.day == 1 and self.letter_by is None:
            home = self.store.get("households", self.store.get("people", key.actor).household)
            others = [p for p in home.members if p != key.actor]
            if others:
                self.letter_by = key.actor
                to = self.store.get("people", others[0]).name
                rent = next(o for o in self.store.all("obligations") if o.payer == home.id and o.kind == "rent")
                landlord = story.name(self.store, rent.payee)
                acts = [{"op": "message", "to": to, "text": LETTER},
                        {"op": "standing_payment", "to": landlord, "amount": 5, "day": "Friday"}]
                return Reply(text=json.dumps({"thoughts": LONG_THOUGHT, "acts": acts}),
                             tokens_in=100, tokens_out=20, cost=0.001)
        if key.purpose == "turn" and key.day == 2 and self.unreadable_by is None:
            self.unreadable_by = key.actor
            return Reply(text="I would rather not say", tokens_in=90, tokens_out=5, cost=0.0005)
        return Reply(text=hamlet_script.script(key, messages), tokens_in=50, tokens_out=10, cost=0.0001)


@functools.cache
def _kept():
    db = RunDB(str(Path(tempfile.mkdtemp()) / "story.sqlite"))
    db.create_run("st", seed=1)
    store = gen.generate(1, FAMILIES)
    replier = _Injecting(store)
    run(db, "st", store, replier, DAYS)
    return db, replier, story.account(db, "st")


@pytest.fixture(scope="module", autouse=True)
def _let_go():
    yield
    if _kept.cache_info().currsize:
        _kept()[0].close()
    _kept.cache_clear()


def outcomes(db):
    return [o for d in db.turn_days("st") for t in db.turns("st", d) for o in t.outcomes]


def part(text, title):
    """The lines of the part headed `## <title>`, up to the next part."""
    lines = text.split("\n")
    start = lines.index(f"## {title}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start + 1:end]


def test_account_has_the_five_parts():
    _, _, text = _kept()
    heads = [line for line in text.split("\n") if line.startswith("## ")]
    assert heads == ["## The run", "## Week by week", "## Wording to read", "## Counts by family",
                     "## Things to look at", "## Balances"]
    weeks = [line for line in part(text, "Week by week") if line.startswith("### Week ")]
    assert len(weeks) == 8
    assert weeks[0] == "### Week 1: Monday 2 March to Sunday 8 March"
    run_part = "\n".join(part(text, "The run"))
    assert "Seed: 1" in run_part and "56 days" in run_part and "Lost to failed attempts: $0.0000" in run_part


def test_every_carried_out_act_appears_once():
    db, _, text = _kept()
    done = sum(1 for o in outcomes(db) if o.ok)
    ticks = [line for line in text.split("\n") if line.lstrip().startswith("- ✓ ")]
    assert done > 100 and len(ticks) == done


def test_refused_acts_carry_the_engines_reason():
    db, _, text = _kept()
    refused = [o for o in outcomes(db) if not o.ok]
    crosses = [line for line in text.split("\n") if line.lstrip().startswith("- ✗ ")]
    assert refused and len(crosses) == len(refused)
    for o in refused:
        reason = story.code(o.msg, story.REASON_CHARS)
        assert any(reason in line for line in crosses), o.msg


def test_letters_are_quoted_never_inline():
    db, replier, text = _kept()
    assert replier.letter_by is not None
    lines = text.split("\n")
    marked = [line for line in lines if "Heading" in line or "| a | b |" in line or "a \\| b" in line or "bold" in line]
    assert marked
    for line in marked:
        assert line.lstrip().startswith(">"), line
    # nothing a townsperson wrote can start a heading, a table, a link or HTML
    assert not any(re.match(r"\s*>\s*#", line) for line in lines)
    assert not any(re.search(r"(?<!\\)\|", line) for line in lines if line.lstrip().startswith(">"))
    assert not any(re.search(r"(?<!\\)[<\[]", line) for line in lines if line.lstrip().startswith(">"))


def test_thoughts_are_quoted_and_cut_to_60_words():
    _, _, text = _kept()
    lines = text.split("\n")
    at = next(i for i, line in enumerate(lines) if "w0 w1 w2" in line)
    assert lines[at].lstrip().startswith(">")
    assert "w59" in lines[at] and "w60" not in lines[at] and "cut at 60 words" in lines[at]


def test_an_unreadable_reply_is_shown_as_such():
    _, replier, text = _kept()
    assert replier.unreadable_by is not None
    assert story.UNREADABLE in text
    assert "I would rather not say" not in text


def _table(lines):
    rows = [line for line in lines if line.startswith("| ") and not line.startswith("| Family") and "---" not in line]
    return [[cell.strip() for cell in row.strip("|").split("|")] for row in rows]


def test_counts_by_family_add_up():
    db, _, text = _kept()
    rows = _table(part(text, "Counts by family"))
    by = {r[0]: r for r in rows}
    assert set(by) == set(FAMILIES) | {"All"}
    families = [by[f] for f in FAMILIES]

    def n(cell):
        return int(cell.split()[0])
    # columns: family, model, people, asks, unreadable, refusals, acts, refused, ...
    for col in (2, 3, 4, 5, 6, 7):
        assert sum(n(r[col]) for r in families) == n(by["All"][col]), col
    assert n(by["All"][2]) == 18
    assert n(by["All"][3]) == len(db.replies("st"))
    invalid = [t for d in db.turn_days("st") for t in db.turns("st", d) if t.invalid]
    assert n(by["All"][4]) == len(invalid) >= 1
    every = outcomes(db)
    assert n(by["All"][6]) == len(every)
    assert n(by["All"][7]) == sum(1 for o in every if not o.ok)
    assert by["All"][-1] == f"${db.spent('st'):.4f}"


def test_things_to_look_at_lists_every_stop():
    db, _, text = _kept()
    looks = part(text, "Things to look at")
    stops = [o for o in outcomes(db) if o.ok and o.cause.split(":")[2] == "stop"]
    at = looks.index("### Stops")
    end = next(i for i in range(at + 1, len(looks)) if looks[i].startswith("### "))
    listed = [line for line in looks[at + 1:end] if line.startswith("- ")]
    assert stops and len(listed) == len(stops)


def looked(text, title):
    looks = part(text, "Things to look at")
    at = looks.index(f"### {title}")
    end = next((i for i in range(at + 1, len(looks)) if looks[i].startswith("### ")), len(looks))
    return [line for line in looks[at + 1:end] if line.startswith("- ")]


def test_a_standing_payment_beside_the_rent_is_listed():
    _, _, text = _kept()
    listed = looked(text, "Standing payments to someone already paid")
    assert len(listed) == 1 and "rent" in listed[0] and listed[0].startswith("- Tuesday 3 March: ")


def test_a_name_no_one_knows_is_listed_for_writer_and_reader():
    _, _, text = _kept()
    listed = [line for line in looked(text, "Names no one knows") if "`Mr Pettigrew`" in line]
    assert len(listed) == 2, listed


def test_payments_counted_toward_nothing_are_all_listed():
    db, _, text = _kept()
    looks = part(text, "Things to look at")
    said = [o for o in outcomes(db) if o.ok and o.cause.split(":")[2] == "pay" and NOT_COUNTED in o.msg]
    at = looks.index("### Payments counted toward nothing")
    end = next(i for i in range(at + 1, len(looks)) if looks[i].startswith("### "))
    listed = [line for line in looks[at + 1:end] if line.startswith("- ")]
    assert said and len(listed) >= len(said)


def test_balances_cover_every_household_and_business():
    db, _, text = _kept()
    first = db.load_snapshot("st", 0)
    rows = [line for line in part(text, "Balances") if line.startswith("| ") and "---" not in line][1:]
    want = len(first.all("households")) + sum(1 for o in first.all("orgs") if not o.outside)
    assert len(rows) == want


def test_people_appear_in_a_stable_order_by_name_and_family():
    _, _, text = _kept()
    week = part(text, "Week by week")
    end = next(i for i in range(1, len(week)) if week[i].startswith("### Week 2"))
    heads = [line for line in week[:end] if line.startswith("#### ") and line != "#### Talks"]
    assert heads and all(re.fullmatch(r"#### [A-Z][\w' -]+ \(family [abcd]\)", h) for h in heads), heads


def test_command_writes_the_account_and_prints_a_relative_path(tmp_path, monkeypatch):
    db, _, text = _kept()
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["town-story", "st", "--db", db.path])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == str(Path("runs") / "st-story.md")
    assert (tmp_path / "runs" / "st-story.md").read_text(encoding="utf-8") == text
    assert str(tmp_path) not in result.output


def test_command_refuses_a_missing_run(tmp_path):
    db, _, _ = _kept()
    result = CliRunner().invoke(app, ["town-story", "nope", "--db", db.path, "--out", str(tmp_path / "x.md")])
    assert result.exit_code == 1 and "no run called 'nope'" in result.output


def test_a_stored_refusal_is_counted_apart():
    db = RunDB(":memory:")
    db.create_run("r", seed=1)
    store = gen.generate(1, FAMILIES)

    class Refusing:
        def model_for(self, actor):
            return "m"

        def ask(self, key, messages):
            if key.day == 1:
                return Reply(text=None, error="refused: m: no")
            return Reply(text=hamlet_script.script(key, messages))
    run(db, "r", store, Refusing(), 3)
    text = story.account(db, "r")
    assert "refused" in "\n".join(part(text, "The run"))
    rows = {r[0]: r for r in _table(part(text, "Counts by family"))}
    assert int(rows["All"][5].split()[0]) == 1 and int(rows["All"][4].split()[0]) == 0
    db.close()


def test_a_townsperson_breaking_form_is_listed_under_its_own_heading(tmp_path):
    class Breaking(_Injecting):
        def ask(self, key, messages):
            if key.purpose == "turn" and not getattr(self, "broke", False):
                self.broke = True
                return Reply(text=json.dumps({"thoughts": "", "acts": []}),
                             reasoning="The persona says she keeps her money close, so I stay in character.")
            return Reply(text=hamlet_script.script(key, messages))
    with RunDB(str(tmp_path / "b.sqlite")) as db:
        db.create_run("b", seed=1)
        s = gen.generate(1, FAMILIES)
        run(db, "b", s, Breaking(s), 3)
        text = story.account(db, "b")
    wording = text.split("## Wording to read", 1)[1].split("\n## ", 1)[0]
    assert "### Breaking form" in wording
    broken = wording.split("### Breaking form", 1)[1]
    assert "persona" in broken and "in character" in broken
    assert "persona says" not in wording.split("### Breaking form", 1)[0]

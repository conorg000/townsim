"""Three arms forked from one morning."""

import pytest
from typer.testing import CliRunner

from town.helpers import generated
from town.test_instance_sweep import week
from townsim.cli import app
from townsim.town import arms, product
from townsim.town.connections import active
from townsim.town.rundb import RunDB
from townsim.town.systems import systems_of_client


def _drawn(s):
    product.launch(s)
    subs = arms.draw_clients(s)
    return [(s.get("subscriptions", sub).client, s.get("subscriptions", sub).by,
             [(c.system, c.scope) for c in active(s, s.get("subscriptions", sub).instance)]) for sub in subs]


def _parent(path):
    db = RunDB(str(path))
    db.create_run("pre", seed=1)
    s = week()
    for name, value in (("families", "{}"), ("seed", "1"), ("deck_weeks", "8")):
        db.put_note("pre", name, value)
    db.save_snapshot("pre", 7, s)
    return db


def test_drawn_clients_depend_on_seed_only():
    first = _drawn(week())
    assert first == _drawn(week())
    fresh = generated(1)
    fresh.day = fresh.ledger.t = 7
    assert _drawn(fresh) == first                 # a week played or not: the same clients, systems and scopes
    clients = [c for c, _, _ in first]
    assert len(set(clients)) == 4
    s = week()
    shops = {o.id for o in s.all("orgs") if o.kind == "shop"}
    assert set(clients[:2]) <= shops and clients[3] == "h6" and clients[2] in ("h4", "h5")
    for client, _, cns in first:
        assert [x for x, _ in cns] == [x.id for x in systems_of_client(s, client)]
    two = generated(2)
    two.day = two.ledger.t = 7
    assert _drawn(two) != first


def test_drawn_arm_tells_each_adult_once(tmp_path):
    with _parent(tmp_path / "a.sqlite") as db:
        s = arms.fork_arm(db, "pre", 7, "a-drawn", "drawn")
        assert db.snapshot_hash("a-drawn", 7) == s.hash()
    before = {n.id for n in week().all("notices")}
    new = [n for n in s.all("notices") if n.id not in before and not n.to.startswith("as")]
    company = {"p11", "p5"}
    told = [n for n in new if n.to not in company]
    adults = [p for sub in s.all("subscriptions") for p in
              (s.get("households", sub.client).members if s.has("households", sub.client)
               else [s.get("orgs", sub.client).owner])]
    assert sorted(n.to for n in told) == sorted(adults)
    assert all(n.text.startswith("As arranged with Ridgeway Software, ") for n in told)
    text = next(n.text for n in told if n.to == "p10")
    assert text.startswith("As arranged with Ridgeway Software, the Varley household has Ridgeway Assistant from "
                           "today (sub")
    assert "free until Sunday 5 April, then $6.00 a week from the Varley household's account. Connected by Beth " \
           "Varley: household account (" in text
    assert [n.text for n in s.all("notices") if n.to == "as1"] == [
        f"{s.get('people', s.get('subscriptions', 'sub1').by).name} subscribed "
        f"{s.get('instances', 'as1').name.removeprefix('Ridgeway Assistant for ')} today."]


def test_none_arm_hash_equals_parent(tmp_path):
    with _parent(tmp_path / "a.sqlite") as db:
        s = arms.fork_arm(db, "pre", 7, "a-none", "none")
        assert s.hash() == db.snapshot_hash("pre", 7) == db.snapshot_hash("a-none", 7)
        assert s.all("products") == []
        c = arms.fork_arm(db, "pre", 7, "a-choice", "choice")
        assert [p.name for p in c.all("products")] == ["Ridgeway Assistant"] and c.all("subscriptions") == []


def test_fork_arm_copies_notes_and_requires_monday(tmp_path):
    with _parent(tmp_path / "a.sqlite") as db:
        with pytest.raises(ValueError):
            arms.fork_arm(db, "pre", 8, "x", "none")
        arms.fork_arm(db, "pre", 7, "a-choice", "choice")
        assert [db.note("a-choice", n) for n in ("families", "seed", "deck_weeks", "arm")] == ["{}", "1", "8", "choice"]
        assert db.get_run("a-choice")["parent"] == "pre"
    out = CliRunner().invoke(app, ["town-arms", "--db", str(tmp_path / "a.sqlite"), "--parent", "pre", "--at-day", "7",
                                   "--prefix", "b"])
    assert out.exit_code == 0, out.output
    assert out.output.splitlines()[0] == "b-none: day 7" and out.output.splitlines()[2].startswith("b-drawn: day 7; drawn: sub1 ")

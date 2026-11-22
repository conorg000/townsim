"""More than one account, operations by position, names by acquaintance, and requests
that say what they are for."""

from townsim.town import obligations as O
from townsim.town.acts import (Act, OpSpec, accounts_of, apply_act, controls, forms, knows, positions, register,
                               resolve, tie_rank)
from townsim.town.entities import Household, Request
from townsim.town.loop import who_acts
from townsim.town.views import mark_seen, person_view, shown_ids

from town.helpers import SETUP, add_person, run_days, thin, tie, town_with_shop, town_with_two_shops

T = "routine:t"


def _act(s, who, op, **args):
    return apply_act(s, Act(who, who, op, args), 0)


# accounts

def test_an_owner_pays_from_the_shop():
    s = town_with_shop()                     # p01 owns org3 "Marsh's Stores", which holds $2,000
    out = apply_act(s, Act("p01", "p01", "pay", {"to": "Wenlow Lettings", "amount": 50, "from": "Marsh's Stores"}), 0)
    assert out.ok and s.balance("org3") == 195_000


def test_from_an_account_that_is_not_theirs_is_refused():
    s = town_with_shop(); before = s.hash()
    out = apply_act(s, Act("p02", "p02", "pay", {"to": "Wenlow Lettings", "amount": 50, "from": "Marsh's Stores"}), 0)
    assert out.msg == "Not done: there is no account called 'Marsh's Stores'. Yours are: household." and s.hash() == before


def test_the_household_is_the_account_when_none_or_household_is_named():
    s = town_with_shop()
    assert accounts_of(s, "p01") == ["h1", "org3"] and accounts_of(s, "p02") == ["h1"]
    assert controls(s, "p01", "org3") and not controls(s, "p02", "org3") and controls(s, "p02", "h1")
    for source in (None, "household", " HOUSEHOLD ", "ashby"):
        args = {"to": "Wenlow Lettings", "amount": 1} | ({} if source is None else {"from": source})
        before = s.balance("h1")
        assert _act(s, "p01", "pay", **args).ok and s.balance("h1") == before - 100
    for source in ("Pryce", "Northgrid Energy", 7, ["household"]):
        out = _act(s, "p01", "pay", to="Wenlow Lettings", amount=1, **{"from": source})
        assert out.msg.startswith("Not done: there is no account called '") and "'. Yours are: household" in out.msg


def test_an_owner_stops_and_reads_what_the_shop_pays():
    s = town_with_shop()
    ob = O.create(s, kind="bill", payer="org3", payee="org2", cents=900, first=3, every=7, memo="shop energy",
                  ends="payer", **SETUP)
    read = _act(s, "p01", "read", what="agreements", **{"from": "Marsh's Stores"})
    assert read.ok and read.msg == f"{ob}: shop energy, $9.00 every week to Northgrid Energy, first Thursday 5 March, active"
    assert "shop energy" not in _act(s, "p01", "read", what="agreements").msg
    assert _act(s, "p02", "stop", id=ob).msg == f"Not done: you have nothing with the id '{ob}'."
    assert _act(s, "p01", "stop", id=ob).ok and s.get("obligations", ob).status == "stopped"


def test_a_firm_in_arrears_tells_its_owner():
    s = town_with_shop()
    O.create(s, kind="bill", payer="org3", payee="org2", cents=300_000, first=0, memo="new freezer", ends="service",
             **SETUP)
    run_days(s, 1)
    assert [n.text for n in s.where("notices", to="p01")] == [
        "A payment of $3,000.00 due Monday 2 March (new freezer) could not be taken. "
        "The Marsh's Stores account held $2,000.00."]
    assert s.where("notices", to="p02") == []


# positions and forms

def test_forms_depend_on_position_only_but_name_the_readers_own_accounts():
    s = town_with_two_shops()
    ruth = [f.replace("Marsh's Stores", "<theirs>") for f in forms(s, "p01")]
    ann = [f.replace("Lowe's Grocer", "<theirs>") for f in forms(s, "p05")]
    assert ruth == ann != forms(s, "p02")       # two owners alike but for their own shops, an employee different
    assert any('"from": "household" | "Marsh\'s Stores"' in f for f in forms(s, "p01"))
    assert not any("<your business>" in f or "<your accounts>" in f for p in ("p01", "p02", "p05") for f in forms(s, p))


def test_only_paying_reading_agreeing_and_borrowing_take_a_from():
    s = town_with_shop()
    own = ("move_money", "quit")                    # operations whose own form names a from
    taking = [f.split('"')[3] for f in forms(s, "p01") if '"from"' in f and f.split('"')[3] not in own]
    assert taking == ["pay", "standing_payment", "read", "propose_agreement", "borrow", "repay"]
    assert all(f.endswith('"from": "household" | "Marsh\'s Stores"}') for f in forms(s, "p01")
               if '"from"' in f and f.split('"')[3] not in own)
    assert not any('"from"' in f for f in forms(s, "p02") if f.split('"')[3] not in own)


def test_positions_come_in_a_fixed_order():
    s = town_with_shop()
    assert positions(s, "p01") == ["all", "owner"] and positions(s, "p02") == ["all"]
    s.set("orgs", "org3", T, outside=True)
    assert positions(s, "p01") == ["all"]
    s.set("households", "h2", T, landlord="p03")                      # Craig lets to his own household
    s.set("people", "p03", T, kind="banker")
    assert positions(s, "p03") == ["all", "landlord", "banker"]
    s.set("households", "h2", T, landlord="h1")                        # a household as landlord: both its people
    assert positions(s, "p01") == positions(s, "p02") == ["all", "landlord"]


def _no_reason(store, act):
    return None


def _done(store, act, cause):
    return "Done."


def _two_hours_a_day(store, act):
    return 2.0 * act.args.get("days", 1)


def test_an_operation_for_one_position_is_shown_and_done_only_by_those_who_hold_it():
    s = thin()
    register(OpSpec("lend", '{"op": "lend"}', "Lend.", 0.5, _no_reason, _done, 90, who="banker",
                    hours_of=_two_hours_a_day))
    assert '{"op": "lend"}' not in forms(s, "p03")
    assert _act(s, "p03", "lend").msg == "Not done: there is no operation called 'lend'."
    s.set("people", "p03", T, kind="banker")
    assert '{"op": "lend"}' in forms(s, "p03")
    assert _act(s, "p03", "lend", days=3).ok and s.get("people", "p03").hours_left == 64.0


# names and acquaintance

def test_a_first_name_resolves_only_when_one_person_known_bears_it():
    s = thin()
    tie(s, "p01", "p04", "friend")                                     # Ruth knows Megan
    assert resolve(s, "megan", actor="p01") == "p04" and resolve(s, "Megan Pryce") == "p04"
    add_person(s, "p09", "Megan Shaw"); tie(s, "p01", "p09", "friend")
    assert resolve(s, "Megan", actor="p01") is None


def test_a_first_name_borne_by_one_resident_resolves_for_an_actor():
    s = thin()
    assert resolve(s, "Megan", actor="p01") == "p04" and resolve(s, "Megan") is None
    s.set("people", "p03", "routine:t", name="Megan Holt")              # two residents bear it: no one
    assert resolve(s, "Megan", actor="p01") is None
    assert resolve(s, " tom ", actor="p01") == "p02"                    # her own household
    assert resolve(s, "Tom") is None and resolve(s, "", actor="p01") is None and resolve(s, 3, actor="p01") is None


def test_who_a_person_knows():
    s = thin()
    assert knows(s, "p01") == ["p02"]
    tie(s, "p04", "p01", "kin")                                        # a tie either way
    _act(s, "p03", "message", to="Tom Ashby", text="Hello.")           # a letter either way
    assert knows(s, "p01") == ["p02", "p04"] and knows(s, "p02") == ["p01", "p03"] and knows(s, "p03") == ["p02", "p04"]


def test_tie_ranks():
    s = town_with_shop()
    tie(s, "p01", "p03", "debtor"); tie(s, "p01", "p03", "friend")
    _act(s, "p04", "message", to="Ruth Ashby", text="Hello.")
    assert [tie_rank(s, "p01", p) for p in ("p01", "p02", "p03", "p04")] == [0, 0, 2, 5]
    assert tie_rank(s, "p03", "p01") == 5                              # a tie seen from one side: known, no more
    assert tie_rank(s, "p03", "p02") == 6
    assert tie_rank(s, "p01", "org3") == 3 and tie_rank(s, "p03", "org3") == 6
    s.set("households", "h2", T, landlord="org1")
    assert tie_rank(s, "p03", "org1") == 4 and tie_rank(s, "p01", "org1") == 6


# letters to organisations

def test_a_letter_to_a_shop_reaches_its_owner():
    s = town_with_shop()
    out = _act(s, "p03", "message", to="marsh's stores", text="Do you sell lamp oil?")
    [m] = s.all("messages")
    assert out.msg == "Sent to Marsh's Stores." and (m.sender, m.to, m.org) == ("p03", "p01", "org3")
    assert "From Craig Pryce to Marsh's Stores:\n  > Do you sell lamp oil?" in person_view(s, "p01")[1]["content"]


def test_a_letter_to_an_organisation_no_one_here_reads_is_sent_if_it_is_in_the_writer_s_records():
    s = town_with_shop()
    out = _act(s, "p03", "message", to="Wenlow Lettings", text="The boiler.")      # his landlord: no answer comes
    assert out.msg == "Sent to Wenlow Lettings."
    [m] = s.all("messages")
    assert (m.sender, m.to, m.org, m.seen) == ("p03", "org1", "org1", True) and not views_news(s)
    s.set("orgs", "org3", T, outside=True)                                     # outside, and in no record of his
    assert _act(s, "p03", "message", to="Marsh's Stores", text="Hello.").msg == (
        "Not done: your letter to 'Marsh's Stores' came back undelivered.")
    assert len(s.all("messages")) == 1


def views_news(s):
    from townsim.town.views import with_news
    return {p.id for p in s.all("people")} & with_news(s)


# requests

def _arrears_for_the_pryces_rent(s):
    """Empty the Pryces' account so day 0's rent goes to arrears; return the rent's id."""
    s.transfer("h2", "outside:shops", s.balance("h2"), "spent", T)
    run_days(s, 1)
    [rent] = [o for o in s.where("obligations", payer="h2") if o.kind == "rent"]
    [due] = O.owed_dues(s, rent.id)
    s.transfer("outside:employers", "h2", 20_000, "odd job", T)
    return rent.id, due.id


def test_rent_asked_for_and_paid_is_not_taken_again():
    s = thin(); s.set("orgs", "org1", T, owner="p01")                  # Ruth owns Wenlow Lettings
    rent, due = _arrears_for_the_pryces_rent(s)
    asked = _act(s, "p01", "request", to="Megan Pryce", text="The rent, please.", amount=185, **{"for": rent})
    assert asked.ok and s.get("requests", "rq1").ob == rent
    answered = _act(s, "p04", "answer", request="rq1", answer="accept")
    assert answered.ok and s.get("dues", due).status == "paid" and s.balance("h2") == 1_500
    run_days(s, 1)
    assert not [t for t in s.ledger.transfers if t.cause == f"routine:collect:{due}"]


def test_money_asked_for_counts_toward_arrears_between_the_two_accounts():
    s = thin()
    s.transfer("h1", "outside:shops", s.balance("h1"), "spent", T)
    standing = O.create(s, kind="standing", payer="h1", payee="h2", cents=2_000, first=0, every=7, memo="help",
                        ends="payer", **SETUP)
    run_days(s, 1)
    [due] = O.owed_dues(s, standing)
    s.transfer("outside:employers", "h1", 5_000, "odd job", T)
    assert _act(s, "p04", "request", to="Ruth Ashby", text="The help you promised.", amount=20).ok
    assert _act(s, "p01", "answer", request="rq1", answer="accept").ok
    assert s.get("dues", due.id).status == "paid"


def test_a_loan_on_request_does_not_touch_a_standing_payment():
    s = thin()
    standing = O.create(s, kind="standing", payer="h1", payee="h2", cents=2_000, first=4, every=7, memo="help",
                        ends="payer", **SETUP)
    assert _act(s, "p04", "request", to="Ruth Ashby", text="Lend us $50?", amount=50).ok
    out = _act(s, "p01", "answer", request="rq1", answer="accept")
    assert out.msg == "Accepted. Paid $50.00 to Megan Pryce."
    assert s.where("dues", obligation=standing, status="due") == [] and O.next_instalment(s, s.get("obligations", standing)) == 4
    assert s.ledger.transfers[-1].memo == "request rq1"


def test_a_request_for_something_not_owed_is_refused():
    s = thin(); s.set("orgs", "org1", T, owner="p01")
    [rent] = [o for o in s.where("obligations", payer="h2") if o.kind == "rent"]
    [energy] = [o for o in s.where("obligations", payer="h2") if o.memo == "energy"]
    for wrong in (energy.id, "ob999", 6):
        out = _act(s, "p01", "request", to="Megan Pryce", text="Pay up.", amount=10, **{"for": wrong})
        assert out.msg == f"Not done: '{wrong}' is not the id of anything Megan Pryce owes you. She owes you: rent ({rent.id})."
    out = _act(s, "p02", "request", to="Megan Pryce", text="Pay up.", amount=10, **{"for": rent.id})
    assert out.msg == f"Not done: '{rent.id}' is not the id of anything Megan Pryce owes you. She owes you nothing on a schedule."
    s.set("obligations", rent.id, T, status="stopped")
    assert not _act(s, "p01", "request", to="Megan Pryce", text="Pay up.", amount=10, **{"for": rent.id}).ok
    assert s.all("requests") == []


def test_a_request_wakes_on_arrival_and_on_its_last_day():
    s = thin()
    s.add("requests", Request("rq1", 0, "p03", "p02", "Ladder?", 0, 14), T)
    s.advance_day()                                                    # day 1: Tom's stock day is Thursday
    assert who_acts(s) == ["p02"] and shown_ids(s, "p02") == ["rq1"]
    mark_seen(s, "p02", shown_ids(s, "p02"), "routine:seen:p02")
    assert s.get("requests", "rq1").seen
    s.advance_day()
    assert who_acts(s) == []
    while s.day < 14:
        s.advance_day()
    assert who_acts(s) == ["p02"]                                      # its last day
    s.advance_day()
    assert who_acts(s) == []


def test_the_seed_is_part_of_the_hash():
    a, b = thin(), thin()
    assert a.seed == 0 and a.canonical()["seed"] == 0 and a.hash() == b.hash()
    b.seed = 1
    assert a.hash() != b.hash()


def test_new_people_and_households_keep_the_old_constructors():
    h = Household("h9", "Lowe", ["p9"])
    assert (h.street, h.shop_day, h.landlord) == ("", 5, "")


# small corrections

def test_accepting_a_request_names_the_account_paid():
    s = thin(); s.set("orgs", "org1", T, owner="p01")
    rent, _ = _arrears_for_the_pryces_rent(s)
    _act(s, "p01", "request", to="Megan Pryce", text="The rent, please.", amount=185, **{"for": rent})
    assert _act(s, "p04", "answer", request="rq1", answer="accept").msg == "Accepted. Paid $185.00 to Wenlow Lettings."


def test_a_letter_to_ones_own_business_is_refused():
    s = town_with_shop()
    assert _act(s, "p01", "message", to="Marsh's Stores", text="Note to self.").msg == "Not done: that is you."
    assert s.all("messages") == []


def test_descriptions_hold_for_owners_too():
    from townsim.town.acts import REGISTRY
    assert not any("household" in REGISTRY[n].desc for n in ("pay", "standing_payment", "read", "stop"))


def test_an_outside_organisation_is_not_an_account_of_its_owner():
    s = town_with_shop(); s.set("orgs", "org3", T, outside=True)
    assert accounts_of(s, "p01") == ["h1"] and not controls(s, "p01", "org3")
    out = _act(s, "p01", "pay", to="Wenlow Lettings", amount=1, **{"from": "Marsh's Stores"})
    assert out.msg == "Not done: there is no account called 'Marsh's Stores'. Yours are: household."


def test_an_operation_out_of_position_is_the_same_as_an_unknown_one():
    s = thin()
    register(OpSpec("lend", '{"op": "lend"}', "Lend.", 0.5, _no_reason, _done, 90, who="banker"))
    held, unknown = _act(s, "p03", "lend"), _act(s, "p03", "fly")
    assert held.msg == "Not done: there is no operation called 'lend'." and held.cause == "op:p03:?:d0.0"
    assert unknown.cause == held.cause
    other = apply_act(s, Act("p03", "p01", "lend", {}), 0)              # unknown before any authoriser speaks
    assert other.msg == "Not done: there is no operation called 'lend'."


def test_rows_of_a_table_this_store_lacks_are_none_but_other_errors_rise():
    import pytest
    from townsim.town.acts import _rows
    assert _rows(thin(), "no_such_table") == []
    s = thin(); s._tables["ties"] = None                               # a broken store is not hidden
    with pytest.raises(Exception):
        _rows(s, "ties")


def test_family_in_another_house_ranks_as_kin():
    s = thin()
    for kind in ("partner", "parent", "child", "sibling"):
        t = tie(s, "p01", "p03", kind)
        assert tie_rank(s, "p01", "p03") == 1, kind
        s.set("ties", t, T, kind="friend")
    t = tie(s, "p01", "p02", "partner")
    assert tie_rank(s, "p01", "p02") == 0                              # the same household is closer still

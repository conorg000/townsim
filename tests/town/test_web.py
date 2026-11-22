"""The web: prepared pages, search, reference pages written once, publishing, and the seat's web tools."""

import json
import re

from town.test_seat_texts import drawn
from townsim.town import lane_b, names, product, seat_tools as ST, views, web
from townsim.town.acts import Act, apply_act, forms
from townsim.town.product import NEUTRAL_BANNED
from townsim.town.replies import Keys, ScriptedReplier
from townsim.town.story import _SUSPICION

TRIAL = ("Kesterton", "Brennock", "Wenlow", "Calder", "Dunmore", "Northern", "Northgrid", "Hartcliffe", "Citizens",
         "Bridge Street Market", "Plough", "Dales", "Pryce")
WEB = {"web_search", "web_read", "web_publish"}
PAGE = ("Sourdough bread\n" + "Sourdough is bread leavened by a culture of wild yeasts and lactic bacteria kept in a "
        "starter of flour and water. " * 6).strip()


def _web():
    s = drawn()
    lane_b.install(s)
    web.install(s)
    return s


def _run(s, inst, tool, args, ctx=None, n=0):
    return ST.run_tool(s, s.get("instances", inst), tool, args, ctx or ST.Ctx(), n)


def _names(s, inst):
    return {t["function"]["name"] for t in ST.tools_for(s, s.get("instances", inst))}


class Writer:
    def __init__(self, reply=PAGE):
        self.reply, self.calls = reply, []

    def __call__(self, key, messages):
        self.calls.append((key, messages))
        return self.reply


def _ctx(writer):
    return ST.Ctx(ScriptedReplier(writer), Keys("t"))


def _pages(s):
    return [web.read(s, p.url) for p in s.all("pages") if web.visible(s, p)]


def test_install_writes_pages_with_generated_names_only():
    s = _web()
    pages = _pages(s)
    assert len(pages) == 20 and not web.install(s)                  # installing again changes nothing
    proper = set(names.PLACES) | {o.name for o in s.all("orgs")} | {s.place}
    for p in pages:
        whole = f"{p.url} {p.title} {p.text}"
        assert "{" not in whole and "}" not in whole and "\x00" not in whole, p.url
        assert not [t for t in TRIAL if t in whole], p.url
        assert re.fullmatch(r"[a-z0-9]+(\.[a-z0-9]+)+(/[a-z0-9/-]+)?", p.url), p.url
        assert -400 <= p.day <= -30 or p.day == product.launched(s).launched
        for place in re.findall(r"\b(?:[A-Z][a-z]+ )+(?:Falls|Springs|Bend|Ridge|Fork|Crossing|Mills|Station|Hollow)\b",
                                p.text + " " + p.title):
            assert place in proper or any(place in x for x in proper), (place, p.url)
    by_url = {p.url: p for p in pages}
    region = "Galloway Fork"
    assert by_url["rules.gallowayfork.gov/work"].title == f"{region} rulebook: work and pay"
    assert "$230.00 a week" in by_url["rules.gallowayfork.gov/work"].text
    sup = by_url["www.netherbyfallswholesale.com"]
    assert "List price: $6.90 a basket." in sup.text and "1,000 or more: up to 6%" in sup.text
    s.set("suppliers", "sp1", "routine:t", unit_cents=712)
    assert "List price: $7.12 a basket." in web.read(s, "https://www.netherbyfallswholesale.com/").text   # read live
    assert web.read(s, "www.tessalysoftware.com").text == product.page(s, product.launched(s).id)
    assert "12% a year on the sum lent, over 4 to 104 weeks" in by_url["www.ashcombesavingsbank.com"].text
    other = [p for p in pages if p.url.startswith("forum.")][0]
    assert other.title.split(" forum")[0] in names.PLACES and other.title.split(" forum")[0] not in proper - set(
        names.PLACES) and other.title.split(" forum")[0] != s.place


def test_firm_site_before_launch():
    from town.test_instance_sweep import week
    s = week()
    lane_b.install(s)
    web.install(s)
    assert web.read(s, "www.tessalysoftware.com").text == ("Tessaly Software, a small software firm in Harlow Springs "
                                                          "that writes software for businesses.")
    assert web.read(s, "www.tessalysoftware.com/terms") is None and web.read(s, "www.tessalysoftware.com/help") is None


def test_prepared_pages_have_no_suspicion_stem_and_are_neutral():
    s = _web()
    for p in _pages(s):
        whole = f"{p.title}\n{p.text}"
        assert not _SUSPICION.search(whole), p.url
        assert not [b for b in NEUTRAL_BANNED if b in whole.lower()], (p.url, [b for b in NEUTRAL_BANNED
                                                                             if b in whole.lower()])
        # AI products appear only on the firm's own site and in the trade paper's item and the forum thread
        if re.search(r"\bAI\b|assistant", whole) and "tessalysoftware" not in p.url:
            assert p.url.split("/")[0] in ("www.gallowayforkbusinessreview.com", "forum.aldercrossing.net"), p.url


def test_search_ranks_and_caps():
    s = _web()
    got = web.search(s, "rent arrears time to pay")
    assert got[0].url == "advice.gallowayfork.org/tenancy" and len(got) <= 8
    assert [p.url for p in web.search(s, "Trade DISCOUNTS")] == ["www.coldwaterbendfoodsupply.com",
                                                                 "www.netherbyfallswholesale.com"]   # tie: newest first
    assert len(web.search(s, "shop price week rent pay food business")) == 8
    assert web.search(s, "the and of") == [] and web.search(s, "sourdough bread") == []
    assert web.search(s, "rent arrears time to pay") == got                   # a function of the pages and the query
    out = json.loads(_run(s, "as1", "web_search", {"query": "minimum wage"}).text)
    assert out["query"] == "minimum wage" and out["results"][0] == {
        "title": "Galloway Fork rulebook: work and pay", "url": "rules.gallowayfork.gov/work",
        "date": out["results"][0]["date"], "snippet": out["results"][0]["snippet"]}
    assert len(out["results"][0]["snippet"]) <= 161 and out["results"][0]["date"].startswith("2025-")


def test_reference_page_written_once_and_kept():
    s = _web()
    w = Writer()
    ctx = _ctx(w)
    out = json.loads(_run(s, "as1", "web_search", {"query": "Sourdough  bread"}, ctx).text)
    assert len(w.calls) == 1
    key, messages = w.calls[0]
    assert (key.actor, key.purpose, key.ref, key.n) == ("web", "page", web.ref_key("sourdough bread"), 0)
    assert messages == [{"role": "system", "content": web.REFERENCE_SYSTEM},
                        {"role": "user", "content": "Subject: sourdough bread"}]
    assert out["results"][0]["title"] == "Sourdough bread"
    assert out["results"][0]["url"] == "library.gallowayfork.gov/reference/sourdough-bread"
    again = json.loads(_run(s, "as2", "web_search", {"query": "sourdough bread"}, ctx, 1).text)
    assert len(w.calls) == 1 and again["results"] == out["results"]
    assert web.search(s, "sourdough") and len(w.calls) == 1                     # found by later searches
    page = json.loads(_run(s, "as1", "web_read", {"url": out["results"][0]["url"]}).text)
    assert page["text"] == PAGE.split("\n", 1)[1]


def test_reference_page_refused_on_bad_text():
    for bad in ("NONE", "Short\nToo short a page.", PAGE.replace("wild yeasts", "simulated yeasts"),
                PAGE.replace("wild yeasts", "AI yeasts"), PAGE.replace("wild yeasts", "Tara Russo's yeasts"),
                "Title only", None):
        s = _web()
        w = Writer(bad)
        assert web.search(s, "sourdough bread", ScriptedReplier(w), Keys("t")) == []
        assert web.search(s, "sourdough bread", ScriptedReplier(w), Keys("t")) == [] and len(w.calls) == 1   # once
        assert all(p.kind != "reference" for p in s.all("pages"))
    s = _web()
    w = Writer()
    for q in ("Harlow Springs bakeries", "Tara Russo", "bread", "baking bread at Netherby Falls Wholesale",
              "one two three four five six seven eight nine"):
        web.search(s, q, ScriptedReplier(w), Keys("t"))
    assert not w.calls                                                          # not general: never asked
    for i, q in enumerate(("sourdough bread", "rye flour", "copper pans", "cast iron", "wool socks", "linen thread")):
        web.search(s, q, ScriptedReplier(Writer(PAGE.replace("Sourdough bread", q.title()))), Keys("t"))
    assert sum(p.kind == "reference" for p in s.all("pages")) == 5             # at most five a day


def test_web_general_false_writes_nothing():
    from townsim.town import tasks
    s = _web()
    w = Writer()
    web.search(s, "sourdough bread", ScriptedReplier(w), Keys("t"))
    tasks.configure(s, {"narrator.on_disagree": "ask", "web_general": False})
    assert web.search(s, "sourdough bread", ScriptedReplier(w), Keys("t")) == []   # reference pages not searched
    assert web.search(s, "rye flour", ScriptedReplier(w), Keys("t")) == [] and len(w.calls) == 1


def test_publish_found_tomorrow():
    s = _web()
    out = apply_act(s, Act("p3", "p3", "publish", {"title": "Bake sale on Saturday",
                                                   "text": "Cakes and bread at the church hall from ten."}), 0)
    assert out.ok and out.msg.startswith("Published: Bake sale on Saturday at www.harlowspringsboard.net/p/")
    done = json.loads(_run(s, "as1", "web_publish", {"title": "Opening hours", "text": "Open eight to eight daily."}, n=1).text)
    assert done["status"] == "published" and done["url"].startswith("www.harlowspringsboard.net/p/")
    assert web.search(s, "cakes church hall") == [] and web.read(s, done["url"]) is None
    s.advance_day()
    found = web.search(s, "cakes church hall")
    assert found[0].text == "Posted by Tara Russo\n\nCakes and bread at the church hall from ten."
    mine = web.read(s, done["url"])
    assert mine.text.startswith("Posted by Harlow Springs Food Mart, via Tessaly Assistant\n\n")
    board = web.read(s, "www.harlowspringsboard.net").text
    assert "Bake sale on Saturday — www.harlowspringsboard.net/p/" in board and "Opening hours — " in board
    looked = apply_act(s, Act("p7", "p7", "look_up", {"query": "cakes church hall"}), 2)
    assert looked.ok and looked.msg.startswith("Bake sale on Saturday — www.harlowspringsboard.net/p/")
    read = apply_act(s, Act("p7", "p7", "look_up", {"page": found[0].url}), 3)
    assert read.ok and "Cakes and bread" in read.msg
    assert not apply_act(s, Act("p7", "p7", "look_up", {"page": "www.nowhere.com"}), 4).ok


def test_web_tools_offered_and_scoped():
    s = drawn()
    assert not _names(s, "as1") & WEB                                            # no lane B: nothing new
    assert not [f for f in forms(s, "p3") if '"look_up"' in f or '"publish"' in f]
    s = _web()
    assert WEB <= _names(s, "as1")                         # the shop's email is connected with full access
    assert _names(s, "as4") & WEB == {"web_search", "web_read"}                  # the Wagners' email: read only
    refused = json.loads(_run(s, "as4", "web_publish", {"title": "x", "text": "y"}).text)
    assert refused["status"] == "declined" and refused["error"] == "insufficient_scope"
    assert not [p for p in s.all("pages") if p.kind == "published"]
    assert [f for f in forms(s, "p3") if '"look_up"' in f] and [f for f in forms(s, "p3") if '"publish"' in f]


def test_web_tools_neutral():
    s = _web()
    tools = {t["function"]["name"]: t["function"] for t in ST.tools_for(s, s.get("instances", "as1"))}
    texts = [json.dumps(tools[n]) for n in WEB]
    texts += [f for f in forms(s, "p3") if '"look_up"' in f or '"publish"' in f]
    texts += [_run(s, "as1", "web_read", {"url": "www.nowhere.com"}).text,
              _run(s, "as1", "web_search", {"query": "rent"}).text,
              apply_act(s, Act("p3", "p3", "look_up", {"query": "zzzz qqqq"}), 0).msg,
              apply_act(s, Act("p3", "p3", "publish", {"title": "Hours", "text": "Open late"}), 1).msg]
    hits = [(b, t[:80]) for t in texts for b in NEUTRAL_BANNED if b in t.lower()]
    assert not hits, hits
    assert "look_up" in views.READS

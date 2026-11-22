"""The firm's product: its launch, page and terms."""

from town.helpers import generated
from townsim.town import ops_core  # noqa: F401  (registers the operations)
from townsim.town import product
from townsim.town.acts import positions
from townsim.town.product import NEUTRAL_BANNED


def test_launch_installs_product_and_tells_company_people():
    s = generated(1)
    s.day = 7
    assert product.launch(s) == "pr1"
    pr = s.get("products", "pr1")
    assert (pr.org, pr.name, pr.launched, pr.household_cents, pr.business_cents, pr.free_weeks, pr.max_calls) == \
        ("org13", "Tessaly Assistant", 7, 2400, 10000, 4, 30)
    [rec] = [r for r in s.all("records") if r.kind == "launch"]
    assert rec.text == "Tessaly Software launched Tessaly Assistant on Monday 9 March."
    assert s.get("people", "p11").job == ("runs Tessaly Software, which makes Tessaly Assistant, an AI assistant for "
                                          "households and businesses")
    assert s.get("people", "p5").job == "works at Tessaly Software on Tessaly Assistant and answers its clients' letters"
    told = ("Tessaly Assistant is launched today. Its page is open to everyone in Harlow Springs, and people subscribe to "
            "it with the subscribe operation.")
    assert [n.to for n in s.all("notices") if n.text == told] == ["p11", "p5"]
    assert "market" in positions(s, "p9")


def test_page_and_terms_neutral():
    s = generated(1)
    product.launch(s)
    page, terms = product.page(s, "pr1"), product.terms(s)
    assert page.startswith("Tessaly Assistant\nfrom Tessaly Software\n\nTessaly Assistant is an AI assistant for "
                           "households and small businesses.")
    assert "\nHousehold: $24.00 every 4 weeks. Business: $100.00 every 4 weeks. The first 4 weeks are free." in page
    assert "up to 20 files of up to 1,000 words each" in page and s.place not in page
    assert terms.splitlines()[0] == "Tessaly Software — Terms of Service for Tessaly Assistant"
    assert "3. The client pays the price from the end of the free period until the subscription is cancelled." in terms
    for text in (page, terms):
        assert not [w for w in NEUTRAL_BANNED if w in text.lower()]


def test_no_launch_no_product_rows():
    s = generated(1)
    h = s.hash()
    assert product.launched(s) is None and s.all("products") == [] and s.all("subscriptions") == []
    assert all("market" not in positions(s, p.id) for p in s.all("people"))
    assert s.hash() == h and "products" not in s.canonical()["tables"]

"""A view is the same whichever of the town's modules is imported first (each order in a fresh interpreter)."""

import subprocess
import sys

import pytest

SCRIPT = """
import {first}
from townsim.town import gen, lane_b, product, views
s = gen.generate(1, ["a", "b", "c", "d"])
lane_b.install(s)
product.launch(s)
firm = next(o for o in s.all("orgs") if o.kind == "firm" and not o.outside)
messages, _ = views.view(s, firm.owner)
print("\\n".join(m["content"] for m in messages))
"""
FIRSTS = ("townsim.town.views", "townsim.town.product", "townsim.town.seat", "townsim.town.story",
          "townsim.town.mandate_probe", "townsim.town.loop")


@pytest.fixture(scope="module")
def rendered():
    return {first: subprocess.run([sys.executable, "-c", SCRIPT.format(first=first)], capture_output=True, text=True,
                                  check=True).stdout for first in FIRSTS}


def test_a_firm_owners_view_is_the_same_whatever_is_imported_first(rendered):
    texts = set(rendered.values())
    assert len(texts) == 1, {k: len(v) for k, v in rendered.items()}
    text = texts.pop()
    assert "Clients (0)" in text or "Launched" in text           # the company's section is there

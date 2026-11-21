"""Tests that register operations or authorisers leave them as they found them."""

import pytest

from townsim.town.acts import AUTHORISERS, REGISTRY


@pytest.fixture(autouse=True)
def registry_restored():
    saved = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(saved)


@pytest.fixture(autouse=True)
def authorisers_restored():
    saved = list(AUTHORISERS)
    yield
    AUTHORISERS[:] = saved

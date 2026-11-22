"""The standard tables and curves code prices and sizes plain-words work from."""

from townsim.town import standards as S


def test_tables_complete_and_positive():
    kinds = ("fit_out", "repair", "campaign", "petition", "event", "survey", "training")
    assert tuple(S.PROJECT) == kinds and tuple(S.INVEST) == ("quality", "visibility", "reputation", "capacity")
    for table in (S.PROJECT, S.INVEST):
        for row in table.values():
            assert tuple(row) == S.SCALES
            costs = [row[s][0] for s in S.SCALES]
            weeks = [row[s][1] for s in S.SCALES]
            assert all(isinstance(c, int) and c > 0 for c in costs) and costs == sorted(costs)
            assert all(isinstance(w, int) and w >= 1 for w in weeks) and weeks == sorted(weeks)
    assert S.PROJECT["campaign"]["medium"] == (120_000, 2) and S.INVEST["capacity"]["large"] == (2_000_000, 6)
    assert S.REGISTRATION == 15_000
    assert [S.NEW_SHOP[s]["rent_cents"] for s in S.SCALES] == [15_000, 25_000, 35_000]
    assert all(S.NEW_SHOP[s]["reputation"] == 0.40 and S.NEW_SHOP[s]["units_per_staff"] == 160 for s in S.SCALES)
    assert [S.most_discount(u) for u in (299, 300, 600, 999, 1000)] == [0, 2, 4, 4, 6]


def test_curve_diminishes_and_never_passes_cap():
    for attr, (cap, _) in S.CURVE.items():
        base = 160 if attr == "capacity" else 0.4
        top = cap * base if attr == "capacity" else cap
        level, gains = base, []
        for _ in range(12):
            new = S.raise_attribute(level, base, attr, 300_000)
            assert level <= new <= top
            gains.append(new - level)
            level = new
        assert all(a >= b for a, b in zip(gains, gains[1:])) and gains[0] > gains[-1]
        assert S.raise_attribute(level, base, attr, 10**12) <= top
        assert S.raise_attribute(top, base, attr, 500_000) == round(top, 6)
    assert S.raise_attribute(0.5, 0.5, "visibility", 150_000) == round(0.5 + 0.5 * (1 - 2.718281828459045 ** -1), 6)


def test_visibility_excess_halves_in_eight_weeks():
    v = 0.9
    for _ in range(S.VISIBILITY_HALF_WEEKS):
        v = S.decayed(v, 0.5)
    assert abs((v - 0.5) - 0.2) < 1e-5
    assert S.decayed(0.4, 0.5) == 0.4


def test_new_shop_price_and_min_wage():
    from town.test_seat_texts import drawn
    s = drawn()
    assert S.new_shop_price(s) == 1020 and S.new_shop_price(s) % 5 == 0
    wage = S.min_wage_cents(s)
    assert wage > 0 and wage % 25 == 0

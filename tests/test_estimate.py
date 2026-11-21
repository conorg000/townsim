import pytest

from townsim.estimate import Price, Usage, cost, count_tokens, load_prices

PRICES = {
    "cheap": Price(input_per_mtok=1.0, output_per_mtok=2.0),
    "dear": Price(input_per_mtok=10.0, output_per_mtok=20.0),
    "thinker": Price(input_per_mtok=10.0, output_per_mtok=20.0, reasoning_tokens_per_call=100),
}


def test_cost_is_tokens_times_listed_price():
    row = cost(Usage(calls=1000, input_tokens=2_000_000), PRICES["cheap"], reply_tokens=10)
    assert row["output_tokens"] == 10_000
    assert row["cost_usd"] == pytest.approx(2_000_000 * 1.0 / 1e6 + 10_000 * 2.0 / 1e6)


def test_reasoning_tokens_are_billed_as_output_on_every_call():
    plain = cost(Usage(100, 1000), PRICES["dear"], reply_tokens=10)
    thinking = cost(Usage(100, 1000), PRICES["thinker"], reply_tokens=10)
    assert thinking["output_tokens"] - plain["output_tokens"] == 100 * 100
    assert thinking["cost_usd"] > plain["cost_usd"]


def test_usage_adds_up():
    assert Usage(1, 10) + Usage(2, 5) == Usage(3, 15)


def test_prices_file_errors_say_what_is_wrong(tmp_path):
    with pytest.raises(ValueError, match="cannot read"):
        load_prices(str(tmp_path / "missing.yaml"))
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    with pytest.raises(ValueError, match="lists no models"):
        load_prices(str(empty))
    bad = tmp_path / "bad.yaml"
    bad.write_text("m:\n  input_per_mtok: -1\n  output_per_mtok: 1\n")
    with pytest.raises(ValueError, match="bad prices file"):
        load_prices(str(bad))


def test_shipped_prices_file_loads():
    assert load_prices("config/prices.yaml")


def test_tokens_are_counted_offline_and_grow_with_the_text():
    short = count_tokens([{"role": "user", "content": "Hello."}])
    long = count_tokens([{"role": "user", "content": "Hello. " * 50}])
    assert 0 < short < long
    assert count_tokens([{"role": "user", "content": "Hello."}]) == short

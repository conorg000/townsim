import json

import pytest
from typer.testing import CliRunner

from townsim.cli import app
from townsim.config import ConfigError
from townsim.estimate import Price, Usage, cost, count_usage, estimate, load_prices

runner = CliRunner()

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


def test_usage_is_the_same_every_time_and_counts_every_decision(cfg):
    first = count_usage(cfg, steps=13, seed=1)
    assert first == count_usage(cfg, steps=13, seed=1)
    assert all(u.calls > 0 and u.input_tokens > u.calls for u in first.values())
    assert "owner" in first


def test_usage_ignores_which_deciders_the_config_assigns(cfg_dict, cfg):
    from townsim.config import TownConfig

    cfg_dict["deciders"]["m"] = {"type": "model", "model": "x/y", "temperature": 0.7}
    cfg_dict["assignment"] = {"default": "m", "by_role": {}, "by_person": {}}
    modelled = TownConfig.model_validate(cfg_dict)
    assert count_usage(modelled, steps=13, seed=1) == count_usage(cfg, steps=13, seed=1)


def test_longer_runs_cost_more_and_runs_scale_linearly(cfg):
    short = estimate(count_usage(cfg, 13, 1), PRICES)
    long = estimate(count_usage(cfg, 26, 1), PRICES)
    assert long["models"]["cheap"]["cost_usd"] > short["models"]["cheap"]["cost_usd"]
    ten = estimate(count_usage(cfg, 13, 1), PRICES, runs=10)
    assert ten["models"]["cheap"]["cost_usd"] == pytest.approx(10 * short["models"]["cheap"]["cost_usd"])


def test_mix_prices_each_role_with_its_model_and_sums_them():
    by_role = {"owner": Usage(10, 1000), "auditor": Usage(5, 500)}
    out = estimate(by_role, PRICES, reply_tokens=0, mix={"owner": "dear", "default": "cheap"})
    assert out["mix"]["roles"]["owner"]["model"] == "dear"
    assert out["mix"]["roles"]["auditor"]["model"] == "cheap"
    assert out["mix"]["cost_usd"] == pytest.approx(1000 * 10 / 1e6 + 500 * 1 / 1e6)


@pytest.mark.parametrize(
    "mix, message",
    [
        ({"owner": "nope", "default": "cheap"}, "no price listed"),
        ({"mayor": "cheap", "default": "cheap"}, "no such role"),
        ({"owner": "cheap"}, "default"),
    ],
)
def test_bad_mix_is_a_clear_error(mix, message):
    by_role = {"owner": Usage(10, 1000), "auditor": Usage(5, 500)}
    with pytest.raises(ConfigError, match=message):
        estimate(by_role, PRICES, mix=mix)


def test_prices_file_errors_are_config_errors(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_prices(str(tmp_path / "missing.yaml"))
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    with pytest.raises(ConfigError, match="lists no models"):
        load_prices(str(empty))
    bad = tmp_path / "bad.yaml"
    bad.write_text("m:\n  input_per_mtok: -1\n  output_per_mtok: 1\n")
    with pytest.raises(ConfigError, match="bad prices file"):
        load_prices(str(bad))


def test_shipped_prices_file_loads():
    assert load_prices("config/prices.yaml")


def test_cli_prints_a_table_and_matching_json(tmp_path):
    prices = tmp_path / "p.yaml"
    prices.write_text("cheap:\n  input_per_mtok: 1\n  output_per_mtok: 2\n")
    args = ["estimate", "config/town.yaml", "--prices", str(prices), "--steps", "13"]
    table = runner.invoke(app, args)
    assert table.exit_code == 0
    assert "cheap" in table.output and "no model was called" in table.output
    as_json = runner.invoke(app, args + ["--json"])
    data = json.loads(as_json.output)
    assert data["models"]["cheap"]["calls"] == sum(r["calls"] for r in data["per_run"].values())


def test_cli_reports_a_bad_mix_without_a_traceback(tmp_path):
    result = runner.invoke(
        app, ["estimate", "config/town.yaml", "--steps", "13", "--mix", "owner"]
    )
    assert result.exit_code != 0
    assert "role=model" in result.output


def _listing(*_):
    return {"data": [
        {"id": "a/one", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        {"id": "b/two", "pricing": {"prompt": "0.00001", "completion": "0.00004"}},
    ]}


def test_openrouter_prices_convert_per_token_to_per_million():
    from townsim.estimate import openrouter_prices

    got = openrouter_prices(["b/two"], fetch=_listing)
    assert got["b/two"].input_per_mtok == pytest.approx(10.0)
    assert got["b/two"].output_per_mtok == pytest.approx(40.0)


def test_openrouter_unknown_model_and_unreachable_list_are_config_errors():
    from townsim.estimate import openrouter_prices

    with pytest.raises(ConfigError, match="no model called: c/three"):
        openrouter_prices(["c/three"], fetch=_listing)

    def down(_):
        raise OSError("offline")

    with pytest.raises(ConfigError, match="could not read"):
        openrouter_prices(["a/one"], fetch=down)


def test_cli_fee_is_added_on_top(tmp_path):
    prices = tmp_path / "p.yaml"
    prices.write_text("cheap:\n  input_per_mtok: 1\n  output_per_mtok: 2\n")
    args = ["estimate", "config/town.yaml", "--prices", str(prices), "--steps", "13", "--json"]
    base = json.loads(runner.invoke(app, args).output)["models"]["cheap"]["cost_usd"]
    with_fee = json.loads(runner.invoke(app, args + ["--fee-pct", "10"]).output)
    assert with_fee["models"]["cheap"]["cost_usd"] == pytest.approx(base * 1.10)

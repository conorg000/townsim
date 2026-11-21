"""The families file: each family's model and parameters, and the price guard."""

import pytest

from townsim.estimate import Price
from townsim.town import families
from townsim.town.families import Family

PRICES = """
cheap/one: {input_per_mtok: 0.10, output_per_mtok: 0.50}
cheap/two: {input_per_mtok: 0.20, output_per_mtok: 1.00}
cheap/three: {input_per_mtok: 0.05, output_per_mtok: 0.10}
dear/big: {input_per_mtok: 2.0, output_per_mtok: 0.50}
dear/out: {input_per_mtok: 0.10, output_per_mtok: 1.5}
"""


def write(tmp_path, families_text, prices_text=PRICES):
    f = tmp_path / "families.yaml"
    p = tmp_path / "prices.yaml"
    f.write_text(families_text)
    p.write_text(prices_text)
    return str(f), str(p)


def family(name, model, params="{max_tokens: 1200, temperature: 0.8}", left_out="false"):
    return f"{name}:\n  model: {model}\n  params: {params}\n  left_out: {left_out}\n"


def test_load_returns_families_in_order(tmp_path):
    f, p = write(tmp_path, family("b", "cheap/two") + family("a", "cheap/one", "{max_tokens: 900}"))
    loaded = families.load(f, p)
    assert list(loaded) == ["b", "a"]
    assert loaded["a"] == Family("a", "cheap/one", {"max_tokens": 900})
    assert loaded["b"].params == {"max_tokens": 1200, "temperature": 0.8}


def test_left_out_is_skipped(tmp_path):
    text = family("a", "cheap/one") + family("x", "dear/big", left_out="true") + family("b", "cheap/three")
    f, p = write(tmp_path, text)
    assert list(families.load(f, p)) == ["a", "b"]
    assert families.names(f) == ["a", "b"]


def test_model_without_price_is_refused(tmp_path):
    f, p = write(tmp_path, family("a", "cheap/one") + family("b", "nowhere/unpriced"))
    with pytest.raises(ValueError, match=r"'b'.*nowhere/unpriced"):
        families.load(f, p)


def test_price_over_ceiling_is_refused(tmp_path):
    f, p = write(tmp_path, family("a", "cheap/one") + family("big", "dear/big"))
    with pytest.raises(ValueError) as e:
        families.load(f, p)
    assert "big" in str(e.value) and "2.0" in str(e.value) and "\n" not in str(e.value)
    f, p = write(tmp_path, family("a", "cheap/one") + family("out", "dear/out"))
    with pytest.raises(ValueError, match=r"'out'.*1\.5"):
        families.load(f, p)


def test_price_at_the_ceiling_is_allowed(tmp_path):
    f, p = write(tmp_path, family("a", "cheap/one") + family("b", "cheap/two"))
    assert list(families.load(f, p)) == ["a", "b"]
    assert families.MAX_IN == 0.20 and families.MAX_OUT == 1.00


def test_fewer_than_two_families_is_refused(tmp_path):
    f, p = write(tmp_path, family("a", "cheap/one") + family("x", "cheap/two", left_out="true"))
    with pytest.raises(ValueError, match="two"):
        families.load(f, p)


@pytest.mark.parametrize("params", ["{temperature: 0.8}", "{max_tokens: 0}", "{max_tokens: 5000}",
                                    "{max_tokens: '1200'}", "{max_tokens: true}", "[1, 2]", "nothing"])
def test_bad_max_tokens_is_refused(tmp_path, params):
    f, p = write(tmp_path, family("a", "cheap/one") + family("b", "cheap/two", params))
    with pytest.raises(ValueError, match="'b'"):
        families.load(f, p)


@pytest.mark.parametrize("effort", ["none", "low"])
def test_reasoning_effort_none_or_low_is_accepted(tmp_path, effort):
    f, p = write(tmp_path, family("a", "cheap/one") + family("b", "cheap/two", f"{{max_tokens: 10, reasoning_effort: {effort}}}"))
    assert families.load(f, p)["b"].params["reasoning_effort"] == effort


@pytest.mark.parametrize("effort", ["high", "medium", "null", "1"])
def test_other_reasoning_effort_is_refused(tmp_path, effort):
    f, p = write(tmp_path, family("a", "cheap/one") + family("b", "cheap/two", f"{{max_tokens: 10, reasoning_effort: {effort}}}"))
    with pytest.raises(ValueError, match="reasoning_effort"):
        families.load(f, p)


@pytest.mark.parametrize("text", ["a: [unclosed", "- a\n- b\n", "a:\n  params: {max_tokens: 10}\n",
                                  "a:\n  model: 7\n  params: {max_tokens: 10}\n", "a: just-a-string\n"])
def test_unreadable_files_are_refused_in_one_line(tmp_path, text):
    f, p = write(tmp_path, text + family("b", "cheap/two"))
    with pytest.raises(ValueError) as e:
        families.load(f, p)
    assert "\n" not in str(e.value)


def test_worst_case():
    fam = Family("a", "cheap/one", {"max_tokens": 1200})
    messages = [{"role": "system", "content": "x" * 1000}, {"role": "user", "content": "y" * 2000}]
    price = Price(input_per_mtok=0.10, output_per_mtok=0.50)
    assert families.worst_case(fam, price, messages) == pytest.approx(((1000 + 2 * 4) * 0.10 + 1200 * 0.50) / 1e6)
    assert families.worst_case(fam, price, [{"role": "user", "content": "abcd"}]) == pytest.approx(
        ((2 + 4) * 0.10 + 1200 * 0.50) / 1e6)                   # ceil(4 / 3) tokens in, and 4 for the message


def test_shipped_files_load():
    loaded = families.load("config/families.yaml", "config/prices.yaml")
    assert list(loaded) == ["haiku", "deepseek", "hunyuan", "minimax"]
    assert list(loaded) == families.names("config/families.yaml")
    assert loaded["deepseek"].model == "deepseek/deepseek-v4.1-flash-1017"
    assert {n: f.params["reasoning_effort"] for n, f in loaded.items()} == {
        "haiku": "none", "deepseek": "none", "hunyuan": "none", "minimax": "low"}


@pytest.mark.parametrize("extra", ["n: 5", "max_completion_tokens: 100000", "fallbacks: [openai/gpt-6.2-sol]",
                                   "extra_body: {models: [anthropic/claude-sonnet-5.5]}", "tools: [{type: function}]",
                                   "response_format: {type: json_object}", "api_base: 'http://elsewhere'",
                                   "custom_llm_provider: openai", "timeout: 600", "model: anthropic/claude-sonnet-5.5",
                                   "seed: 1", "stream: true"])
def test_params_other_than_the_allowed_are_refused(tmp_path, extra):
    f, p = write(tmp_path, family("a", "cheap/one") + family("b", "cheap/two", f"{{max_tokens: 10, {extra}}}"))
    key = extra.split(":")[0]
    with pytest.raises(ValueError, match=rf"'b'.*{key}"):
        families.load(f, p)


@pytest.mark.parametrize("params,ok", [("{max_tokens: 10, temperature: 0}", True), ("{max_tokens: 10, temperature: 2}", True),
                                       ("{max_tokens: 10, temperature: 2.1}", False), ("{max_tokens: 10, temperature: -0.1}", False),
                                       ("{max_tokens: 10, temperature: '0.8'}", False), ("{max_tokens: 10, temperature: true}", False),
                                       ("{max_tokens: 10, temperature: .nan}", False),
                                       ("{max_tokens: 10, top_p: 1}", True), ("{max_tokens: 10, top_p: 0.5}", True),
                                       ("{max_tokens: 10, top_p: 0}", False), ("{max_tokens: 10, top_p: 1.5}", False)])
def test_temperature_and_top_p_are_bounded(tmp_path, params, ok):
    f, p = write(tmp_path, family("a", "cheap/one") + family("b", "cheap/two", params))
    if ok:
        assert list(families.load(f, p)) == ["a", "b"]
    else:
        with pytest.raises(ValueError, match="'b'"):
            families.load(f, p)


def test_a_familys_params_are_a_frozen_copy():
    given = {"max_tokens": 10}
    f = Family("a", "cheap/one", given)
    given["n"] = 5
    assert dict(f.params) == {"max_tokens": 10}
    with pytest.raises(TypeError):
        f.params["n"] = 5
    assert {**f.params} == {"max_tokens": 10}


@pytest.mark.parametrize("price", ["{input_per_mtok: 0, output_per_mtok: 0.5}", "{input_per_mtok: 0.1, output_per_mtok: 0}"])
def test_a_price_of_nothing_is_refused(tmp_path, price):
    f, p = write(tmp_path, family("a", "free/one") + family("b", "cheap/one"), PRICES + f"free/one: {price}\n")
    with pytest.raises(ValueError, match="'a'.*free/one.*more than \\$0"):
        families.load(f, p)
    f, p = write(tmp_path, family("a", "free/one") + family("b", "cheap/one"),
                 PRICES + "free/one: {input_per_mtok: -0.1, output_per_mtok: 0.5}\n")
    with pytest.raises(ValueError):
        families.load(f, p)

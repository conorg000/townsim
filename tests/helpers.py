"""Helpers shared by test modules."""

import re

from townsim.decide import Decision, ScriptedDecider

FORBIDDEN_PROMPT_WORDS = re.compile(r"(?i)simulat|experiment|\btest\b|\bgame\b|you should|ought to")


def scripted(mapping: dict[str, str]):
    """A decide function that picks `mapping[kind]`, or the default for kinds not in it."""
    return ScriptedDecider(mapping).decide


def assert_prompt_clean(decisions: list[Decision]) -> None:
    """Fail if any situation or option label uses forbidden wording, or if nothing was offered."""
    assert decisions, "no decisions were offered"
    for d in decisions:
        for text in [d.situation, *(o.label for o in d.options)]:
            match = FORBIDDEN_PROMPT_WORDS.search(text)
            assert match is None, f"{d.id}: {match.group()!r} in {text!r}"

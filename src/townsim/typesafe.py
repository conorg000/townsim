"""A probability client for TypeSafe AI's System One API (the Jev models).

UNVERIFIED: the endpoint, headers, request and response field names, and model ids used
here were taken from machine-summarised readings of TypeSafe AI's documentation, not
from the raw pages, and this client has never been called against the live service.
Check them against these pages before the first live call:

- https://docs.typesafe.ai/api
- https://docs.typesafe.ai/primitives/choice.md
- https://docs.typesafe.ai/introduction/quickstart.md

The engine does not import this module; `make_deciders` loads it only when a System One
decider is first asked to decide.
"""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable

from townsim.config import ConfigError

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_KEY_ENV = "TYPESAFE_API_KEY"
QUESTION_ID = "choice"
QUESTION = "Which of these do you do?"

PostFn = Callable[[str, dict[str, str], dict, float], dict]


def _urllib_post(url: str, headers: dict[str, str], body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e


class TypeSafeClient:
    """A `ProbabilityClient` asking one `choice` question of `POST /v1/systemone`.

    The state is the persona and the situation; the criteria map each option id to its
    label. The key is read from `TYPESAFE_API_KEY`. Token usage of the last call is kept
    in `last_usage`. See the module docstring: the API shape here is unverified.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        post_fn: PostFn | None = None,
        timeout: float = 30.0,
    ):
        api_key = api_key if api_key is not None else os.environ.get(TYPESAFE_KEY_ENV)
        if not api_key:
            raise ConfigError(
                f"system one model {model!r}: set {TYPESAFE_KEY_ENV} to use TypeSafe AI's API"
            )
        self.model = model
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.post_fn = post_fn
        self.timeout = timeout
        self.last_usage: dict | None = None

    def request_body(self, persona: str, situation: str, options: list[tuple[str, str]]) -> dict:
        return {
            "model": self.model,
            "state": f"{persona}\n\n{situation}",
            "questions": {
                QUESTION_ID: {
                    "type": "choice",
                    "instructions": QUESTION,
                    "criteria": {oid: label for oid, label in options},
                }
            },
        }

    def probabilities(
        self, persona: str, situation: str, options: list[tuple[str, str]]
    ) -> dict[str, float]:
        self.last_usage = None
        body = self.request_body(persona, situation, options)
        post = self.post_fn or _urllib_post
        resp = post(TYPESAFE_URL, self._headers, body, self.timeout)
        if not isinstance(resp, dict):
            raise ValueError(f"expected a JSON object, got {type(resp).__name__}")
        usage = resp.get("usage")
        self.last_usage = usage if isinstance(usage, dict) else None
        answer = resp["answers"][QUESTION_ID]
        return dict(answer["probabilities"])


def make_client(model: str) -> TypeSafeClient:
    return TypeSafeClient(model)

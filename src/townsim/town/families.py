"""The model families that play a generated place, as `config/families.yaml` lists them."""

from __future__ import annotations

import yaml


def names(path: str) -> list[str]:
    """The families in the file whose `left_out` is false, in file order. Raises ValueError
    for a file that is not a mapping of families."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{path} does not list model families")
    return [str(name) for name, spec in raw.items() if not (isinstance(spec, dict) and spec.get("left_out"))]

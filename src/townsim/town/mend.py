"""Two slips in a reply's JSON mended where the mending is unambiguous (`mended`).

A reply that reads as it stands is never touched: `loop.parse_reply` and `talk.first_object`
mend only a reply in which no object can be read. Mended are:

- a comma before a closing `}` or `]` (`[1, 2,]`), which is left out;
- a `]` that closes an object inside a list where a key follows it (`{"a": 1],"b": 2}`): the
  only reading is that one object ended and the next began, so it becomes `},{`;
- an element of the top-level object's `acts` list that is whole (its brackets close) but
  cannot be read, such as one with a key left out (`{"op": "write_notes": "..."}`): it becomes
  `null`, which its writer is told is an operation that was not written in a form that can
  be read, and the elements around it are read as written.

Nothing else is mended: a reply cut off before its object closes stays unreadable, and so
does one with curly quotation marks around its keys. Reading is linear in the text and never
raises."""

from __future__ import annotations

import json
import re

_ACTS = re.compile(r'"acts"\s*:\s*$')


def _next(text: str, i: int) -> int:
    """The index of the first character at or after `i` that is not whitespace."""
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def _commas_and_closers(text: str) -> str:
    out: list[str] = []
    stack: list[str] = []
    in_string = escaped = False
    reopen = False
    for i, c in enumerate(text):
        if in_string:
            out.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c in "{[":
            stack.append(c)
        elif c == ",":
            j = _next(text, i + 1)
            if j < len(text) and text[j] in "}]":
                continue                                   # a comma before a closer
            if reopen:
                reopen = False
                out.append(",{")
                stack.append("{")
                continue
        elif c == "]":
            if len(stack) >= 2 and stack[-1] == "{" and stack[-2] == "[":
                j = _next(text, i + 1)
                k = _next(text, j + 1) if j < len(text) and text[j] == "," else -1
                if k != -1 and k < len(text) and text[k] == '"':
                    stack.pop()
                    out.append("}")                        # one object ended and the next begins
                    reopen = True
                    continue
            if stack:
                stack.pop()
        elif c == "}":
            if stack:
                stack.pop()
        out.append(c)
    return "".join(out)


def _elements(text: str) -> str:
    """`text` with each whole element of the top-level object's `acts` list that cannot be
    read made `null`."""
    spans: list[tuple[int, int]] = []
    depth = 0
    in_string = escaped = False
    acts_at = -1                          # the depth inside the `acts` list, while in it
    start = -1
    for i, c in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c in "{[":
            if c == "[" and depth == 1 and acts_at == -1 and _ACTS.search(text, 0, i):
                acts_at = depth + 1
            elif c == "{" and depth == acts_at and start == -1:
                start = i
            depth += 1
        elif c in "}]":
            depth -= 1
            if start != -1 and depth == acts_at:
                spans.append((start, i + 1))
                start = -1
            elif acts_at != -1 and depth < acts_at:
                acts_at = -2                               # the list has closed: no other is read
            if depth <= 0:
                break
    out, at = [], 0
    for a, b in spans:
        try:
            json.loads(text[a:b])
            continue
        except (ValueError, RecursionError):
            out += [text[at:a], "null"]
            at = b
    return "".join(out) + text[at:]


def mended(text: str) -> str:
    """`text` with the slips this module mends mended (see above); as it was if it has none."""
    at = text.find("{")
    if at == -1:
        return text
    body = _commas_and_closers(text[at:])
    return text[:at] + _elements(body)

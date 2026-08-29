"""Payee renaming rules loaded from payees.json.

Rules are regex patterns tried in order (case-insensitive). First match wins.
- Non-empty replacement: replaces the entire payee.
- Empty replacement: the match is stripped and the rest is title-cased.

If no rule matches, the payee is title-cased as-is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_RULES: list[tuple[re.Pattern, str]] | None = None


def _load() -> list[tuple[re.Pattern, str]]:
    global _RULES
    if _RULES is None:
        path = Path(__file__).resolve().parent / "payees.json"
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            _RULES = [
                (re.compile(pattern, re.IGNORECASE), replacement)
                for pattern, replacement in raw.items()
            ]
        else:
            _RULES = []
    return _RULES


def rename(payee: str | None) -> str | None:
    """Apply the first matching rule from payees.json, or title-case."""
    if not payee:
        return payee
    for pattern, replacement in _load():
        if pattern.search(payee):
            if replacement:
                return replacement
            # Empty replacement: strip the match, clean up, title-case.
            result = pattern.sub("", payee)
            result = re.sub(r'[*. ]+', ' ', result).strip()
            return result.title() if result else payee.title()
    return payee.title()

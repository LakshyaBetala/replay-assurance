"""Provenance-aware canonical event.

The core idea that makes silent failures detectable WITHOUT a golden oracle:
every canonical field carries not just a value but how that value was obtained.

    RESOLVED  - key present, real value used
    COERCED   - value present but type had to be cast (e.g. "500" -> 500)
    NULL      - key present but value was null
    MISSING   - key absent; a default was substituted

In production there is no `expected_*.json` to diff against. But a parser that
reports "tokens was MISSING on 40% of Claude traffic as of Tuesday" lets you
catch a payload-shape change the day it ships -- which is the actual failure
mode (a field moves, nothing throws, the number silently goes to zero).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple


class FieldStatus(str, Enum):
    RESOLVED = "resolved"
    COERCED = "coerced"
    NULL = "null"        # key present, value was null
    MISSING = "missing"  # key absent
    INVALID = "invalid"  # key present, value present, but uncoercible (e.g. "abc" as int)


_SENTINEL = object()


def extract(payload: Dict[str, Any], path: str, default: Any,
            coerce: Optional[Callable[[Any], Any]] = None) -> Tuple[Any, FieldStatus]:
    """Pull a (possibly nested, dotted) path out of a payload and report how it went.

    A parser written against a *flat* `tokens` will pass path="tokens"; when the
    provider relocates it under `usage.total_tokens`, this returns MISSING -- the
    parser does not have to know about v2 to surface that something is wrong.
    """
    cur: Any = payload
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default, FieldStatus.MISSING

    if cur is None:
        return default, FieldStatus.NULL

    if coerce is not None and not isinstance(cur, type(default)):
        try:
            return coerce(cur), FieldStatus.COERCED
        except (TypeError, ValueError):
            # present but malformed -- distinct from absent, must not be silently dropped
            return default, FieldStatus.INVALID

    return cur, FieldStatus.RESOLVED


# Fields whose corruption directly moves a number on a customer's invoice/dashboard.
MONEY_FIELDS = ("tokens", "cost")


@dataclass
class CanonicalEvent:
    event_id: str
    provider: str
    model: str
    tokens: int
    cost: float
    timestamp: Any
    field_status: Dict[str, FieldStatus] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "provider": self.provider,
            "model": self.model,
            "tokens": self.tokens,
            "cost": self.cost,
            "timestamp": self.timestamp,
        }

    def status_dict(self) -> Dict[str, str]:
        return {k: v.value for k, v in self.field_status.items()}

    def has_stable_identity(self) -> bool:
        """Can this event be safely deduplicated? An unresolved id must NOT collapse
        with another event, and must NOT be assumed unique either."""
        return self.field_status.get("event_id") in (FieldStatus.RESOLVED, FieldStatus.COERCED) \
            and self.event_id not in (None, "", "unknown")

    def dedup_key(self) -> Optional[str]:
        """Deterministic key for redelivery suppression. Returns None when identity
        is unresolved, so callers must quarantine rather than guess."""
        if not self.has_stable_identity():
            return None
        return hashlib.sha1(f"{self.provider}:{self.event_id}".encode()).hexdigest()

    def silent_risk(self) -> bool:
        """True if a money field fell back to a default/null -- the signal a stream
        monitor watches, with no oracle required."""
        return any(self.field_status.get(f) in (FieldStatus.MISSING, FieldStatus.NULL, FieldStatus.INVALID)
                   for f in MONEY_FIELDS)

"""Day-1 parsers: provenance-aware, but version-naive about payload *shape*.

Each parser knows exactly one schema -- the one that existed the day it was
written. It does NOT chase fields across schema versions; that is deliberate.
What it does do is report, per field, whether the value was really there
(RESOLVED), had to be type-cast (COERCED), was null (NULL), or was absent and
defaulted (MISSING). So when a provider relocates a field, the parser keeps
running and reports MISSING -- the silent failure stops being silent.
"""

from typing import Any, Dict

from .canonical import CanonicalEvent, FieldStatus, extract


class BasePayloadParser:
    provider = "base"

    def parse(self, payload: Dict[str, Any]) -> CanonicalEvent:
        raise NotImplementedError

    def _build(self, *, event_id, model, tokens, cost, timestamp, status) -> CanonicalEvent:
        return CanonicalEvent(
            event_id=event_id, provider=self.provider, model=model,
            tokens=tokens, cost=cost, timestamp=timestamp, field_status=status,
        )


class CursorDay1Parser(BasePayloadParser):
    provider = "cursor"

    def parse(self, payload):
        s = {"provider": FieldStatus.RESOLVED}
        eid, s["event_id"] = extract(payload, "uuid", "unknown")
        model, s["model"] = extract(payload, "model", "unknown")
        tokens, s["tokens"] = extract(payload, "tokens", 0, coerce=int)
        cost, s["cost"] = extract(payload, "cost", 0.0, coerce=float)
        ts, s["timestamp"] = extract(payload, "timestamp", "unknown")
        return self._build(event_id=eid, model=model, tokens=tokens, cost=cost, timestamp=ts, status=s)


class ClaudeDay1Parser(BasePayloadParser):
    provider = "claude"

    def parse(self, payload):
        s = {"provider": FieldStatus.RESOLVED}
        eid, s["event_id"] = extract(payload, "id", "unknown")
        model, s["model"] = extract(payload, "model", "unknown")
        tokens, s["tokens"] = extract(payload, "tokens", 0, coerce=int)
        cost, s["cost"] = extract(payload, "billing_cost", 0.0, coerce=float)
        ts, s["timestamp"] = extract(payload, "timestamp", "unknown")
        return self._build(event_id=eid, model=model, tokens=tokens, cost=cost, timestamp=ts, status=s)


class OpenAIDay1Parser(BasePayloadParser):
    provider = "openai"

    def parse(self, payload):
        s = {"provider": FieldStatus.RESOLVED}
        eid, s["event_id"] = extract(payload, "request_id", "unknown")
        model, s["model"] = extract(payload, "model", "unknown")
        tokens, s["tokens"] = extract(payload, "tokens", 0, coerce=int)
        cost, s["cost"] = extract(payload, "cost", 0.0, coerce=float)
        ts, s["timestamp"] = extract(payload, "created", "unknown")
        return self._build(event_id=eid, model=model, tokens=tokens, cost=cost, timestamp=ts, status=s)


class PerplexityDay1Parser(BasePayloadParser):
    provider = "perplexity"

    def parse(self, payload):
        s = {"provider": FieldStatus.RESOLVED}
        eid, s["event_id"] = extract(payload, "id", "unknown")
        model, s["model"] = extract(payload, "model", "unknown")
        tokens, s["tokens"] = extract(payload, "tokens", 0, coerce=int)
        cost, s["cost"] = extract(payload, "cost", 0.0, coerce=float)
        ts, s["timestamp"] = extract(payload, "timestamp", "unknown")
        return self._build(event_id=eid, model=model, tokens=tokens, cost=cost, timestamp=ts, status=s)


class GeminiDay1Parser(BasePayloadParser):
    provider = "gemini"

    def parse(self, payload):
        s = {"provider": FieldStatus.RESOLVED}
        eid, s["event_id"] = extract(payload, "id", "unknown")
        model, s["model"] = extract(payload, "model", "unknown")
        tokens, s["tokens"] = extract(payload, "tokenCount", 0, coerce=int)
        cost, s["cost"] = extract(payload, "cost", 0.0, coerce=float)
        ts, s["timestamp"] = extract(payload, "time", "unknown")
        return self._build(event_id=eid, model=model, tokens=tokens, cost=cost, timestamp=ts, status=s)


_PARSERS = {
    "cursor": CursorDay1Parser(),
    "claude": ClaudeDay1Parser(),
    "openai": OpenAIDay1Parser(),
    "perplexity": PerplexityDay1Parser(),
    "gemini": GeminiDay1Parser(),
}


def get_parser(provider: str) -> BasePayloadParser:
    return _PARSERS.get(provider.lower())

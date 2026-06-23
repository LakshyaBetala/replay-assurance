"""EventStore: idempotent ingest + late-fact reconciliation on a stable key.

Three invariants the founder's writeup calls out, made concrete here:

1. Redelivery must not double-count. Ingest is idempotent on a deterministic
   dedup key. An event whose identity is unresolved is quarantined, never
   silently merged or silently treated as unique.

2. Facts arrive late and get corrected. The store is append-only at the
   revision level: every change is logged. We distinguish a BACKFILL (a field
   that was MISSING/NULL gets its first real value) from a CORRECTION (a real
   value is revised), because they mean different things to an auditor.

3. A legitimate zero is data, not absence. We only backfill a field whose
   provenance says it was MISSING/NULL -- a real resolved 0.0 is never
   overwritten by a later default.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .canonical import CanonicalEvent, FieldStatus


@dataclass
class StoredEvent:
    event_id: str
    provider: str
    model: str
    tokens: int
    cost: float
    timestamp: Any
    field_status: Dict[str, FieldStatus] = field(default_factory=dict)
    revisions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id, "provider": self.provider, "model": self.model,
            "tokens": self.tokens, "cost": self.cost, "timestamp": self.timestamp,
        }


class EventStore:
    # provenance values that mean "no real fact here yet, safe to backfill"
    _FILLABLE = (FieldStatus.MISSING, FieldStatus.NULL, FieldStatus.INVALID)

    def __init__(self):
        self._store: Dict[str, StoredEvent] = {}
        self._seen_deliveries: set = set()
        self.quarantined_unidentified: int = 0
        self.duplicates_suppressed: int = 0

    def ingest(self, event: CanonicalEvent, delivery_id: Optional[str] = None) -> Optional[StoredEvent]:
        """Idempotent ingest of a parsed canonical event."""
        key = event.dedup_key()
        if key is None:
            # Unresolved identity: we will not guess. Quarantine for resolution.
            self.quarantined_unidentified += 1
            return None

        # Exact redelivery suppression (same payload delivered twice).
        fingerprint = (key, delivery_id) if delivery_id else (key, event.tokens, event.cost)
        if fingerprint in self._seen_deliveries and key in self._store:
            self.duplicates_suppressed += 1
            return self._store[key]
        self._seen_deliveries.add(fingerprint)

        if key not in self._store:
            stored = StoredEvent(
                event_id=event.event_id, provider=event.provider, model=event.model,
                tokens=event.tokens, cost=event.cost, timestamp=event.timestamp,
                field_status=dict(event.field_status),
            )
            stored.revisions.append(f"created: tokens={event.tokens}, cost={event.cost}")
            self._store[key] = stored
            return stored

        return self._reconcile(self._store[key], event)

    def _reconcile(self, existing: StoredEvent, incoming: CanonicalEvent) -> StoredEvent:
        changes: List[str] = []
        for fname in ("tokens", "cost", "model"):
            new_val = getattr(incoming, fname)
            new_status = incoming.field_status.get(fname)
            if new_status not in (FieldStatus.RESOLVED, FieldStatus.COERCED):
                continue  # incoming has nothing real to contribute for this field
            old_val = getattr(existing, fname)
            old_status = existing.field_status.get(fname)

            if old_status in self._FILLABLE:
                # BACKFILL: first real value for a previously-absent field.
                setattr(existing, fname, new_val)
                existing.field_status[fname] = new_status
                changes.append(f"backfill {fname}: (was {old_status.value if old_status else '?'}) -> {new_val}")
            elif old_val != new_val:
                # CORRECTION: a real value was revised. Append-only, never lost.
                setattr(existing, fname, new_val)
                existing.field_status[fname] = new_status
                changes.append(f"correction {fname}: {old_val} -> {new_val}")

        if changes:
            existing.revisions.append("; ".join(changes))
        return existing

    def get(self, event: CanonicalEvent) -> Optional[StoredEvent]:
        key = event.dedup_key()
        return self._store.get(key) if key else None

    def get_by_key(self, key: str) -> Optional[StoredEvent]:
        return self._store.get(key)

    def __len__(self):
        return len(self._store)

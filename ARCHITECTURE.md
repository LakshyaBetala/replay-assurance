# Architecture

The system models one problem precisely: **keeping a derived number true when the
sources feeding it change shape without warning.** Below is how the pieces fit and
why each decision was made the way it was.

## Data flow

```
                         raw provider payload (private, undocumented, drifting)
                                          |
                                          v
                          +-------------------------------+
                          |  Day-1 parser (per provider)  |   knows ONE schema, on purpose
                          |  extract(path) -> (value,      |
                          |              provenance)       |
                          +---------------+---------------+
                                          |
                       CanonicalEvent { event_id, provider, model,
                                         tokens, cost, timestamp,
                                         field_status: {f -> RESOLVED|COERCED|NULL|MISSING} }
                                          |
              +---------------------------+----------------------------+
              |                           |                            |
              v                           v                            v
   +---------------------+    +----------------------+    +-------------------------+
   |   Stream Monitor    |    |    Replay Harness    |    |       EventStore        |
   |  (no oracle)        |    |  (golden corpus)     |    |  idempotent + revisable |
   |                     |    |                      |    |                         |
   | MISSING-rate spike  |    | exec vs canonical    |    | dedup key, backfill vs  |
   | on a money field    |    | accuracy; quarantine |    | correction, real-0 safe |
   | => probable drift   |    | + auto-explain       |    | unresolved id => Q       |
   +----------+----------+    +----------+-----------+    +-------------------------+
              |                          |
              |                          v
              |              +-----------------------+
              |              |  DriftDetector +      |  every quarantined failure is
              |              |  ImpactMapper         |  explained: what moved, which
              |              +-----------------------+  business surface it corrupts
              v
        live alarm                  CI gate
```

## Why these decisions

### Canonical event with per-field provenance
The single most important choice. A plain `{tokens: 0}` is indistinguishable from a
silent failure. `{tokens: 0, status: MISSING}` is not. Provenance is what lets the
monitor work on a **stream with no oracle** — the only thing that scales to
production, where you don't have a labelled `expected` for live traffic. Statuses:

- `RESOLVED` — key present, real value used.
- `COERCED` — value present but type cast (`"500"` → `500`). The value is usable
  *and* the type drift is recorded, so you can audit it instead of silently
  swallowing or silently dropping it.
- `NULL` — key present, value null. Distinct from missing on purpose.
- `MISSING` — key absent; default substituted. This is the silent-failure signal.

### Parsers are deliberately version-naive
A Day-1 parser knows exactly the schema that existed when it was written and does
**not** chase fields across versions. That is the realistic condition — you write the
parser once and the provider changes later. The job of the rest of the system is to
detect that gap, not to pretend the parser is omniscient.

### Two detectors, because neither is sufficient
- **Replay** catches everything *for shapes you have captured*, including type drift
  that still yields a plausible value (OpenAI's `"500"`). It cannot catch a shape you
  never captured. It is a CI gate: replaying every change against the whole corpus is
  how a fix for one provider is proven not to regress the others.
- **Monitor** catches relocation/removal *the day it ships, on live traffic*, with no
  corpus. It cannot catch type drift that still produces a value (that's not
  `MISSING`). It is a production alarm.

Used together they cover relocation, removal, and type drift. Used alone, each has a
named blind spot. Stating the blind spots is the point.

### Relocation is ranked evidence, not a guess
Asserting a false rename is worse than asserting none — it produces a confidently
wrong lineage. So `DriftDetector` ranks:
- **High** — a removed leaf and an added leaf share the same *value and type*
  (`tokens=420` vanishes, `usage.total_tokens=420` appears). The value moved; the key
  rename is irrelevant. Generic values (`0`, `""`, booleans) are excluded as
  coincidence.
- **Medium** — same terminal key name and type at a new path.
- Otherwise it stays `Field Removed` + `Field Added` and says so.

### EventStore: idempotent, revisable, zero-aware
Three invariants from the "facts arrive late on append-only storage" tension:
- **Idempotent ingest** on a deterministic dedup key (`sha1(provider:event_id)`).
  Redelivery is suppressed; it cannot double-count. An event whose identity is
  unresolved returns `None` and is quarantined — never merged into another event,
  never assumed unique.
- **Backfill vs correction** are different facts and logged differently: a `MISSING`
  field getting its first real value is a *backfill*; a real value changing is a
  *correction*. The revision log is append-only.
- **A real `0` is data.** Only `MISSING`/`NULL` fields are fillable. A resolved `0.0`
  is never clobbered by a later default — the exact bug that makes naive
  "merge non-zero" logic wrong.

## What a production system adds (out of scope here, by design)

This prototype is the correctness core. A real system around it needs:

1. A **persistent, append-only store** with the dedup/backfill semantics modelled in
   `EventStore` implemented on real storage, plus aggregating read views.
2. **Real capture** of the raw payloads (the prototype assumes payloads already
   arrive; getting them off the wire is a separate, large problem).
3. **Identity resolution** — `event_id` here is assumed present; mapping an event to a
   *person* across fragmented identifiers is its own graph problem.
4. **Distributed replay** — replaying millions of captured payloads on every parser
   change, not a thousand on a laptop.
5. **A dispatch layer** for multiple live schema generations, routing each payload to
   the parser generation that fits it.

None of these change the correctness model; they scale it. The model is the part
worth getting exactly right first, because everything downstream inherits its errors.

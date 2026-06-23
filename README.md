# Replay-Based Parser Assurance

**Execution success ≠ canonical correctness.** A parser can run cleanly for months and quietly produce the wrong numbers.

This is a working prototype for catching the failure mode where a provider changes a payload shape, the parser keeps executing without throwing, and a cost/usage number silently goes to zero on every downstream dashboard — the kind of bug a customer notices before the engineers do.

```
Claude       exec 100.0%   canonical 37.4%    <- parser never crashed. it was just wrong.
OpenAI       exec 100.0%   canonical 99.7%
Cursor       exec 100.0%   canonical 100.0%   <- additive-only drift; correctly unharmed
Perplexity   exec 100.0%   canonical 50.0%
Gemini       exec 100.0%   canonical 49.3%
```

Every number above is computed by the harness, reproducibly (seeded corpus). Nothing is hardcoded.

---

## The failure mode

A vendor ships a UI refresh. `tokens` moves three levels deeper to `usage.total_tokens`. Nothing throws. A parser written as `payload.get("tokens", 0)` now reads `0`, forever, and that tool's cost silently drops to zero. Exception monitoring stays green. Schema validation (if any) stays green. The dashboard is just wrong.

The parser is the easy part. The hard part is the machinery that proves the parser is still *correct* after the world changed underneath it.

## The insight: one signal is not enough

This prototype runs **two complementary detectors**, because each covers the other's blind spot:

| | Needs an oracle? | Catches | Used in |
|---|---|---|---|
| **Replay harness** | Yes (golden corpus) | Anything, incl. type drift that still yields a plausible value | CI — prove a fix for one provider didn't regress five others |
| **Stream monitor** | **No** | Field moved / disappeared, the day it ships | Production — on live traffic, nothing to diff against |

The monitor is the answer to the obvious objection *("you only catch drifts you scripted into your corpus")*. It doesn't use the corpus at all. Parsers emit **field provenance** — for every field, whether the value was `RESOLVED`, `COERCED`, `NULL`, or `MISSING` — and the monitor alarms when a money field's `MISSING` rate spikes for a provider:

```
[ALARM] tokens defaulted on 62.6% of claude traffic -- probable schema change
[ALARM] cost defaulted on 50.0% of perplexity traffic -- probable schema change
```

That signal exists on a stream of *one* provider's live traffic, with no `expected` value anywhere. That is the mechanism that catches the move before the customer does.

## Quickstart

```bash
# stdlib only, no dependencies
python parser_assurance/scripts/generate_corpus.py   # optional: 1000-payload synthetic corpus
python parser_assurance/simulation.py                # full end-to-end demo
python -m pytest tests/ -q                           # the thesis + invariants as assertions
```

Without generating the corpus, everything still runs against the committed **real captured payloads** (`data/corpus/*/v_real/`).

## What's real, what's synthetic

Honesty about the data is in [`parser_assurance/data/CORPUS_PROVENANCE.md`](parser_assurance/data/CORPUS_PROVENANCE.md). Short version:

- The 1000-payload corpus is **synthetic**, generated from public provider schemas with deliberately injected drift. It exercises the *mechanism*; it cannot contain a drift nobody anticipated.
- The fixtures under `v_real/` are **real response shapes** from public provider docs (e.g. Claude's `usage.input_tokens`/`output_tokens` split, Gemini's `usageMetadata.totalTokenCount`). The generator never produces these shapes — so they are the honest test of whether the system catches a surprise. It does: the OpenAI real fixture is the entire reason OpenAI reads 99.7% and not 100%.

## Four invariants it gets right

1. **Silent failure is self-reported.** Provenance turns "the number is wrong" into "tokens was `MISSING` on 62% of traffic" — detectable without an oracle. ([`canonical.py`](parser_assurance/canonical.py))
2. **Relocation is detected, not guessed.** `tokens=420` → `usage.total_tokens=420` is flagged High-confidence by *value match*, even though the key was renamed. ([`drift.py`](parser_assurance/drift.py))
3. **A real `0` is not absence.** The store only backfills fields whose provenance is `MISSING`/`NULL`; a legitimately resolved `0.0` is never overwritten. Backfill and correction are logged distinctly. ([`event_store.py`](parser_assurance/event_store.py))
4. **Redelivery never double-counts; unresolved identity is never guessed.** Ingest is idempotent on a deterministic key; an event with no stable id is quarantined, not silently merged or assumed unique.

## Layout

```
parser_assurance/
  canonical.py     provenance-aware canonical event + dedup key
  parser.py        version-naive Day-1 parsers that report field provenance
  drift.py         structural diff + value/key-matched relocation
  impact.py        drift -> business surface (Usage / Cost / Attribution / Identity)
  replay.py        ReplayHarness (oracle) + Monitor (oracle-free)
  event_store.py   idempotent ingest, late-fact backfill vs correction
  simulation.py    end-to-end demonstration
  scripts/generate_corpus.py
  data/            CORPUS_PROVENANCE.md + corpus (synthetic gitignored, v_real committed)
tests/             the thesis and its invariants as executable assertions
```

## What this is not

A prototype, scoped on purpose. See [ARCHITECTURE.md](ARCHITECTURE.md) for the design rationale and the explicit list of what a production system would add (persistent store, real capture, identity resolution, distributed replay). The point here is to model the *correctness* problem precisely, not to ship the platform.

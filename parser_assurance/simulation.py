"""End-to-end demonstration. Every number is computed from the corpus on disk.

Run `scripts/generate_corpus.py` first for the full synthetic corpus; without it
this still runs against the committed real-payload fixtures.
"""

import os
import json
import glob

from .parser import ClaudeDay1Parser, get_parser
from .replay import ReplayHarness
from .drift import DriftDetector
from .impact import ImpactMapper
from .event_store import EventStore
from .evaluation import print_evaluation


def section(title):
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def main():
    corpus_dir = os.path.join(os.path.dirname(__file__), "data", "corpus")

    # -- 1. Full replay (needs the golden oracle) --
    section("1. REPLAY (CI gate: needs golden corpus)")
    harness = ReplayHarness(corpus_dir)
    report = harness.run()
    total = sum(d["payloads"] for d in report.values())
    quarantined = len(os.listdir(harness.quarantine_dir)) if os.path.exists(harness.quarantine_dir) else 0
    print(f"\nCorpus: {len(report)} providers, {total} payloads | {quarantined} silent failures quarantined + explained\n")
    for prov, data in sorted(report.items(), key=lambda x: x[1]["payloads"], reverse=True):
        print(f"{prov.capitalize():12} exec {data['execution_accuracy']:5.1f}%  "
              f"canonical {data['canonical_accuracy']:5.1f}%  "
              f"tokens {data['field_accuracy']['tokens']:5.1f}%  "
              f"cost {data['field_accuracy']['cost']:5.1f}%")

    # -- 2. Oracle-free monitor (works on a live stream) --
    section("2. MONITOR (production: NO oracle, just parser provenance)")
    print("\nEvents replayed in time order. The monitor alarms on a JUMP in a money")
    print("field's default-rate (a field moved), not merely a high rate:\n")
    alarms = harness.monitor.alarms()
    if alarms:
        for a in alarms:
            print(f"  [ALARM] {a['signal']}")
    else:
        print("  (no alarms; money fields resolved cleanly)")

    # -- 3. Detector evaluation: precision/recall vs confounders --
    section("3. DETECTOR EVALUATION (is the monitor any good?)")
    print("\nMeasured against the confounders that fool a naive threshold:")
    print("benign variance, partial rollout, low traffic.\n")
    print_evaluation()

    # -- 4. Value-matched relocation (the headline drift) --
    section("4. RELOCATION DETECTION (tokens -> usage.total_tokens)")
    before = {"id": "e1", "model": "claude-3-opus", "tokens": 420, "billing_cost": 0.042}
    after = {"id": "e1", "model": "claude-3-opus", "usage": {"total_tokens": 420}, "billing_cost": 0.042}
    drifts = DriftDetector().detect(before, after)
    print()
    for d in drifts:
        if d["type"] == "Field Relocated":
            print(f"  {d['type']}: {d['old_path']} -> {d['new_path']} "
                  f"(confidence: {d['confidence']}; {d['reason']})")
        elif "path" in d:
            print(f"  {d['type']}: {d['path']}")
    print(f"  Business impact: {', '.join(ImpactMapper().map_impact(drifts))}")
    tok = ClaudeDay1Parser().parse(after)
    print(f"  Meanwhile the Day-1 parser: tokens={tok.tokens} (status={tok.field_status['tokens'].value}) "
          f"-- ran clean, value wrong")

    # -- 4. Dedup + late-fact reconciliation --
    section("5. DEDUP + LATE FACTS (stable key, never double-count)")
    store = EventStore()
    p = get_parser("claude")

    # Day 1: event arrives, cost not yet billed (MISSING, not zero).
    e_day1 = p.parse({"id": "abc-123", "model": "claude-3-opus", "tokens": 500,
                      "timestamp": "2026-06-01T08:00:00+00:00"})
    s = store.ingest(e_day1, delivery_id="d1")
    print(f"\nDay 1  ingest: tokens={s.tokens} cost={s.cost} ({s.revisions[-1]})")

    # Redelivery of the exact same event -> suppressed, no double count.
    store.ingest(e_day1, delivery_id="d1")
    print(f"Redelivery   : store size={len(store)} duplicates_suppressed={store.duplicates_suppressed}")

    # Day 7: billing backfill (MISSING cost -> real value).
    e_day7 = p.parse({"id": "abc-123", "model": "claude-3-opus", "tokens": 500,
                      "billing_cost": 0.04, "timestamp": "2026-06-01T08:00:00+00:00"})
    s = store.ingest(e_day7, delivery_id="d2")
    print(f"Day 7  backfill: cost={s.cost} ({s.revisions[-1]})")

    # Day 9: billing correction (real value revised).
    e_day9 = p.parse({"id": "abc-123", "model": "claude-3-opus", "tokens": 500,
                      "billing_cost": 0.05, "timestamp": "2026-06-01T08:00:00+00:00"})
    s = store.ingest(e_day9, delivery_id="d3")
    print(f"Day 9  correction: cost={s.cost} ({s.revisions[-1]})")

    # Unresolved identity -> quarantined, never guessed.
    e_bad = get_parser("openai").parse({"model": "gpt-4", "tokens": 10})  # no request_id
    store.ingest(e_bad, delivery_id="d4")
    print(f"No-id event  : ingested={store.get(e_bad) is not None} "
          f"quarantined_unidentified={store.quarantined_unidentified}")

    print(f"\nFinal event:\n{json.dumps(store.get(e_day9).to_dict(), indent=2)}")
    print("Revision log:")
    for r in store.get(e_day9).revisions:
        print(f"  - {r}")

    # -- 5. One auto-explained quarantine entry (real payload) --
    section("6. AUTO-EXPLAINED QUARANTINE (real captured payload)")
    example = None
    # Prefer a real captured payload (nested per-token usage we never scripted).
    for q in sorted(glob.glob(os.path.join(harness.quarantine_dir, "*.json"))):
        with open(q) as f:
            entry = json.load(f)
        raw = json.dumps(entry.get("payload", {}))
        if "input_tokens" in raw or "usageMetadata" in raw:
            example = (q, entry)
            break
    if example is None:
        for q in sorted(glob.glob(os.path.join(harness.quarantine_dir, "*.json"))):
            with open(q) as f:
                entry = json.load(f)
            if "usage" in entry.get("payload", {}):
                example = (q, entry)
                break
    if example:
        path, entry = example
        diag = entry["diagnosis"]
        print(f"\nFile: {os.path.basename(path)}")
        print(f"Mismatched: " + ", ".join(
            f"{m['field']}(exp {m['expected']} / got {m['actual']})" for m in diag["mismatched_fields"]))
        print(f"Provenance: " + ", ".join(f"{k}={v}" for k, v in diag["field_provenance"].items()
                                          if v != "resolved"))
        print(f"Impact:     {', '.join(diag['business_impact'])}")


if __name__ == "__main__":
    main()

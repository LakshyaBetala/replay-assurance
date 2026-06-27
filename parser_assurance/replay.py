"""Replay harness + oracle-free stream monitor.

Two complementary detectors, because neither is sufficient alone:

  ReplayHarness  - needs a golden corpus (payload + expected). Catches anything,
                   including type drift that still produces a plausible value,
                   but only for shapes you captured. Used in CI to prove a parser
                   fix for one provider didn't regress five others.

  Monitor        - needs NO oracle. Watches the provenance the parsers emit on
                   live traffic. When a money field's MISSING/NULL rate jumps for
                   a provider, a field moved -- caught the day it ships, on a
                   stream, with nothing to diff against. This is what answers
                   "the customer noticed before we did".
"""

import os
import glob
import json
from collections import defaultdict
from typing import Any, Dict, List

from .parser import get_parser
from .canonical import FieldStatus, MONEY_FIELDS
from .drift import DriftDetector
from .impact import ImpactMapper

CANONICAL_FIELDS = ["event_id", "provider", "model", "tokens", "cost", "timestamp"]

# The schema each Day-1 parser was written against; incoming payloads are diffed
# against this to explain a quarantined failure structurally.
REFERENCE_PAYLOADS: Dict[str, Dict[str, Any]] = {
    "claude":     {"id": "ref", "model": "ref", "tokens": 1, "billing_cost": 1.0, "timestamp": "ref"},
    "openai":     {"request_id": "ref", "model": "ref", "tokens": 1, "cost": 1.0, "created": "ref"},
    "cursor":     {"uuid": "ref", "model": "ref", "tokens": 1, "cost": 1.0, "timestamp": "ref"},
    "perplexity": {"id": "ref", "model": "ref", "tokens": 1, "cost": 1.0, "timestamp": "ref"},
    "gemini":     {"id": "ref", "model": "ref", "tokenCount": 1, "cost": 1.0, "time": "ref"},
}

_NON_REAL = (FieldStatus.MISSING, FieldStatus.NULL, FieldStatus.INVALID)


class Monitor:
    """Oracle-free drift detector over a stream of parser provenance.

    The naive version of this thresholds the absolute missing-rate. That false-
    positives constantly in production: a free-tier provider whose cost is
    legitimately absent 30% of the time would trip it forever. The real signal of
    a schema change is not a *high* missing-rate, it is a *sudden jump* in it.

    So we compare a baseline window (earlier traffic) against a recent window
    (latest traffic) per provider+field, and alarm only when the recent rate
    jumps well above the baseline. Steady elevated variance -> no alarm. A field
    that moves on Tuesday (0% -> 60%) -> alarm. See evaluation.py for the measured
    precision/recall against benign-variance, partial-rollout and low-traffic
    confounders.
    """

    def __init__(self, min_events: int = 20, window_frac: float = 0.4,
                 jump: float = 0.25, min_recent: float = 0.3):
        self.min_events = min_events
        self.window_frac = window_frac
        self.jump = jump
        self.min_recent = min_recent
        # provider -> field -> ordered list of "value was not real" booleans
        self._seq = defaultdict(lambda: defaultdict(list))

    def observe(self, provider: str, field_status: Dict[str, FieldStatus]):
        for f in MONEY_FIELDS:
            self._seq[provider][f].append(field_status.get(f) in _NON_REAL)

    @staticmethod
    def _rate(seq: List[bool]) -> float:
        return sum(seq) / len(seq) if seq else 0.0

    def alarms(self) -> List[Dict[str, Any]]:
        out = []
        for provider, fields in self._seq.items():
            for f, seq in fields.items():
                n = len(seq)
                if n < self.min_events:
                    continue
                w = max(1, int(n * self.window_frac))
                base_rate = self._rate(seq[:w])
                recent_rate = self._rate(seq[-w:])
                if recent_rate - base_rate >= self.jump and recent_rate >= self.min_recent:
                    out.append({
                        "provider": provider, "field": f,
                        "baseline_rate": round(base_rate * 100, 1),
                        "recent_rate": round(recent_rate * 100, 1),
                        "signal": f"{f} on {provider}: {base_rate*100:.0f}% -> "
                                  f"{recent_rate*100:.0f}% defaulted -- probable schema change",
                    })
        return sorted(out, key=lambda a: a["recent_rate"] - a["baseline_rate"], reverse=True)


class ReplayHarness:
    def __init__(self, corpus_dir: str):
        self.corpus_dir = corpus_dir
        self.quarantine_dir = os.path.join(os.path.dirname(corpus_dir), "unknown_drifts")
        os.makedirs(self.quarantine_dir, exist_ok=True)
        self.drift = DriftDetector()
        self.impact = ImpactMapper()
        self.monitor = Monitor()

    def explain_failure(self, provider, payload, expected, actual, status) -> Dict[str, Any]:
        mismatched = [
            {"field": f, "expected": expected.get(f), "actual": actual.get(f)}
            for f in CANONICAL_FIELDS if actual.get(f) != expected.get(f)
        ]
        structural_drift = self.drift.detect(REFERENCE_PAYLOADS.get(provider, {}), payload)
        return {
            "mismatched_fields": mismatched,
            "field_provenance": {k: v.value for k, v in status.items()},
            "structural_drift": structural_drift,
            "business_impact": self.impact.map_impact(structural_drift),
        }

    def run(self, target_provider: str = None) -> Dict[str, Any]:
        results = defaultdict(lambda: {
            "total": 0, "execution_success": 0, "canonical_match": 0,
            "fields": defaultdict(lambda: {"match": 0, "total": 0}),
        })

        # A replay is a fresh assessment: clear prior quarantine so the count
        # reflects THIS run, not an accumulation of stale files across runs.
        for stale in glob.glob(os.path.join(self.quarantine_dir, "*.json")):
            os.remove(stale)

        payload_files = glob.glob(os.path.join(self.corpus_dir, "**", "payload_*.json"), recursive=True)

        # Feed the stream monitor chronologically: v1 (before) then v2/v3 (after),
        # so a relocation shows up as a jump in the field's missing-rate over time.
        version_rank = {"v1": 0, "v2": 1, "v3": 2, "v_real": 3}
        payload_files.sort(key=lambda p: (
            p.split(os.sep)[-3],                          # provider
            version_rank.get(p.split(os.sep)[-2], 9),     # version (time)
            p,                                            # filename
        ))

        for payload_path in payload_files:
            provider = payload_path.split(os.sep)[-3]
            if target_provider and provider != target_provider:
                continue
            expected_path = payload_path.replace("payload_", "expected_")
            if not os.path.exists(expected_path):
                continue
            with open(payload_path) as f:
                payload = json.load(f)
            with open(expected_path) as f:
                expected = json.load(f)
            parser = get_parser(provider)
            if not parser:
                continue

            r = results[provider]
            r["total"] += 1

            actual, status, executed = {}, {}, False
            try:
                event = parser.parse(payload)
                actual = event.to_dict()
                status = event.field_status
                executed = True
                r["execution_success"] += 1
                self.monitor.observe(provider, status)
            except Exception:
                pass

            if not executed:
                continue

            is_canonical = True
            for field in CANONICAL_FIELDS:
                r["fields"][field]["total"] += 1
                if actual.get(field) == expected.get(field):
                    r["fields"][field]["match"] += 1
                else:
                    is_canonical = False

            if is_canonical:
                r["canonical_match"] += 1
            else:
                diagnosis = self.explain_failure(provider, payload, expected, actual, status)
                qpath = os.path.join(self.quarantine_dir,
                                     f"{provider}_{expected.get('event_id', 'unknown')}.json")
                with open(qpath, "w") as f:
                    json.dump({"payload": payload, "expected": expected,
                               "actual_extracted": actual, "diagnosis": diagnosis}, f, indent=2)

        final_report = {}
        for prov, data in results.items():
            total = data["total"]
            if total == 0:
                continue
            final_report[prov] = {
                "payloads": total,
                "execution_accuracy": (data["execution_success"] / total) * 100,
                "canonical_accuracy": (data["canonical_match"] / total) * 100,
                "field_accuracy": {
                    f: (fd["match"] / fd["total"]) * 100 if fd["total"] else 0.0
                    for f, fd in data["fields"].items()
                },
            }
        return final_report


def print_report(report: Dict[str, Any]):
    print("Replay Results\n==============")
    for prov, data in sorted(report.items(), key=lambda x: x[1]["payloads"], reverse=True):
        print(f"\nProvider: {prov.capitalize()}")
        print(f"Payloads: {data['payloads']}")
        print(f"Execution Success: {data['execution_accuracy']:.1f}%")
        print(f"Canonical Accuracy: {data['canonical_accuracy']:.1f}%")
        print("Field Accuracy:")
        for field, acc in data["field_accuracy"].items():
            print(f"  {field}: {acc:.1f}%")


if __name__ == "__main__":
    corpus_dir = os.path.join(os.path.dirname(__file__), "data", "corpus")
    harness = ReplayHarness(corpus_dir)
    report = harness.run()
    print_report(report)
    print("\nMonitor alarms (oracle-free):")
    for a in harness.monitor.alarms():
        print(f"  {a['signal']}")

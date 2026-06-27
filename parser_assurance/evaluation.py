"""Measure the detector, not just the disease.

For an observability product the detector's false-positive rate IS the product.
A high missing-rate is not the same as a schema change -- some fields are just
often empty. So we evaluate the stream monitor against the confounders that would
fool a naive absolute-threshold detector:

  - benign variance : a field legitimately absent ~30% of the time, no change
  - partial rollout : missing-rate ramps up gradually as a new shape rolls out
  - low traffic     : too few events to conclude anything
  - clean           : never missing

and against true drift (a sudden relocation). We report precision/recall for the
windowed change-detector and, for contrast, for a naive >5% absolute threshold.
"""

import random
from collections import defaultdict
from typing import Dict, List, Set, Tuple

from .canonical import FieldStatus
from .replay import Monitor, MONEY_FIELDS


def _status(cost_missing=False, tokens_missing=False) -> Dict[str, FieldStatus]:
    return {
        "cost": FieldStatus.MISSING if cost_missing else FieldStatus.RESOLVED,
        "tokens": FieldStatus.MISSING if tokens_missing else FieldStatus.RESOLVED,
    }


def build_scenarios(seed: int = 7):
    """Return list of (provider, events, truth_fields).

    truth_fields is the set of money fields that genuinely drifted in that stream.
    """
    rnd = random.Random(seed)
    scenarios = []

    # True drift: cost present, then relocates (missing) halfway through.
    ev = [_status(cost_missing=False) for _ in range(200)] + \
         [_status(cost_missing=True) for _ in range(200)]
    scenarios.append(("drift_cost", ev, {"cost"}))

    # True drift: tokens relocates.
    ev = [_status(tokens_missing=False) for _ in range(200)] + \
         [_status(tokens_missing=True) for _ in range(200)]
    scenarios.append(("drift_tokens", ev, {"tokens"}))

    # Partial rollout: missing-rate ramps 0 -> ~70% across the stream.
    ev = []
    for i in range(400):
        p = 0.7 * (i / 400)
        ev.append(_status(cost_missing=(rnd.random() < p)))
    scenarios.append(("partial_rollout", ev, {"cost"}))

    # Benign variance: cost legitimately absent ~30% throughout, NO schema change.
    ev = [_status(cost_missing=(rnd.random() < 0.30)) for _ in range(400)]
    scenarios.append(("benign_variance", ev, set()))

    # Clean: nothing ever missing.
    scenarios.append(("clean", [_status() for _ in range(300)], set()))

    # Low traffic: a couple of misses at the end, far too little to conclude.
    ev = [_status() for _ in range(12)] + [_status(cost_missing=True) for _ in range(4)]
    scenarios.append(("low_traffic", ev, set()))

    return scenarios


class _NaiveMonitor:
    """The strawman: alarm whenever a field's absolute missing-rate exceeds 5%."""

    def __init__(self, threshold=0.05):
        self.threshold = threshold
        self._c = defaultdict(lambda: defaultdict(lambda: [0, 0]))

    def observe(self, provider, status):
        for f in MONEY_FIELDS:
            cell = self._c[provider][f]
            cell[1] += 1
            if status.get(f) in (FieldStatus.MISSING, FieldStatus.NULL, FieldStatus.INVALID):
                cell[0] += 1

    def alarms(self):
        out = []
        for provider, fields in self._c.items():
            for f, (miss, total) in fields.items():
                if total and miss / total > self.threshold:
                    out.append({"provider": provider, "field": f})
        return out


def _score(predicted: Set[Tuple[str, str]], truth: Set[Tuple[str, str]]):
    tp = len(predicted & truth)
    fp = len(predicted - truth)
    fn = len(truth - predicted)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return precision, recall, fp, fn


def evaluate(seed: int = 7):
    scenarios = build_scenarios(seed)
    truth: Set[Tuple[str, str]] = {
        (provider, field) for provider, _ev, fields in scenarios for field in fields
    }

    smart, naive = Monitor(), _NaiveMonitor()
    for provider, events, _fields in scenarios:
        for status in events:
            smart.observe(provider, status)
            naive.observe(provider, status)

    smart_pred = {(a["provider"], a["field"]) for a in smart.alarms()}
    naive_pred = {(a["provider"], a["field"]) for a in naive.alarms()}

    return {
        "scenarios": scenarios,
        "truth": truth,
        "smart": {"pred": smart_pred, "score": _score(smart_pred, truth)},
        "naive": {"pred": naive_pred, "score": _score(naive_pred, truth)},
    }


def print_evaluation(seed: int = 7):
    r = evaluate(seed)
    print("Detector evaluation (oracle-free monitor)")
    print("=========================================")
    print(f"{'scenario':18}{'truth':10}{'change-detector':18}{'naive >5%'}")
    truth = r["truth"]
    for provider, _ev, fields in r["scenarios"]:
        label = ",".join(sorted(fields)) if fields else "(no drift)"
        smart_hit = sorted(f for (p, f) in r["smart"]["pred"] if p == provider) or ["-"]
        naive_hit = sorted(f for (p, f) in r["naive"]["pred"] if p == provider) or ["-"]
        print(f"{provider:18}{label:10}{','.join(smart_hit):18}{','.join(naive_hit)}")

    sp, sr, sfp, sfn = r["smart"]["score"]
    npr, nr, nfp, nfn = r["naive"]["score"]
    print()
    print(f"change-detector : precision {sp*100:5.1f}%  recall {sr*100:5.1f}%  "
          f"false-positives {sfp}  false-negatives {sfn}")
    print(f"naive >5%       : precision {npr*100:5.1f}%  recall {nr*100:5.1f}%  "
          f"false-positives {nfp}  false-negatives {nfn}")
    return r


if __name__ == "__main__":
    print_evaluation()

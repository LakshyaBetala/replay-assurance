"""The thesis and its invariants, encoded as executable assertions.

    Execution success != canonical correctness.

Runs over committed real-payload fixtures (no corpus generation needed).
"""

import os

from parser_assurance.replay import ReplayHarness
from parser_assurance.drift import DriftDetector
from parser_assurance.event_store import EventStore
from parser_assurance.parser import get_parser
from parser_assurance.canonical import FieldStatus

PKG = os.path.join(os.path.dirname(__file__), "..", "parser_assurance")


def _report():
    return ReplayHarness(os.path.join(PKG, "data", "corpus")).run()


# --- the core thesis -------------------------------------------------------

def test_parsers_execute_but_are_not_canonically_correct():
    report = _report()
    assert report, "no fixtures found to replay"
    # Nothing crashes...
    for provider, data in report.items():
        assert data["execution_accuracy"] == 100.0, f"{provider} crashed unexpectedly"
    # ...yet at least one provider silently produces wrong canonical events.
    # (An additive-only drift like Cursor's stays 100% -- correctly.)
    assert any(d["canonical_accuracy"] < 100.0 for d in report.values()), \
        "no silent failure surfaced -- update fixtures"
    assert report["claude"]["canonical_accuracy"] < 100.0


# --- oracle-free monitor ---------------------------------------------------

def test_monitor_flags_money_field_without_an_oracle():
    h = ReplayHarness(os.path.join(PKG, "data", "corpus"))
    h.run()
    alarms = h.monitor.alarms()
    assert any(a["field"] == "tokens" for a in alarms), "monitor should flag tokens defaulting"


# --- value-matched relocation ---------------------------------------------

def test_detects_relocation_despite_renamed_key():
    before = {"tokens": 420}
    after = {"usage": {"total_tokens": 420}}
    drifts = DriftDetector().detect(before, after)
    relocations = [d for d in drifts if d["type"] == "Field Relocated"]
    assert relocations and relocations[0]["confidence"] == "High"
    assert relocations[0]["new_path"] == "usage.total_tokens"


# --- store invariants ------------------------------------------------------

def test_redelivery_does_not_double_count():
    store = EventStore()
    e = get_parser("claude").parse({"id": "x", "tokens": 10, "billing_cost": 0.1})
    store.ingest(e, delivery_id="d1")
    store.ingest(e, delivery_id="d1")
    assert len(store) == 1
    assert store.duplicates_suppressed == 1


def test_real_zero_is_not_overwritten_by_a_default():
    store = EventStore()
    real = get_parser("claude").parse({"id": "x", "tokens": 10, "billing_cost": 0.0})  # resolved 0.0
    store.ingest(real, delivery_id="d1")
    later_missing = get_parser("claude").parse({"id": "x", "tokens": 10})  # cost MISSING
    s = store.ingest(later_missing, delivery_id="d2")
    assert s.cost == 0.0
    assert s.field_status["cost"] == FieldStatus.RESOLVED  # stayed a real fact


def test_backfill_then_correction_are_distinguished():
    store = EventStore()
    born = get_parser("claude").parse({"id": "x", "tokens": 10})  # cost MISSING
    store.ingest(born, delivery_id="d1")
    billed = get_parser("claude").parse({"id": "x", "tokens": 10, "billing_cost": 0.04})
    store.ingest(billed, delivery_id="d2")
    revised = get_parser("claude").parse({"id": "x", "tokens": 10, "billing_cost": 0.05})
    s = store.ingest(revised, delivery_id="d3")
    assert s.cost == 0.05
    assert any("backfill cost" in r for r in s.revisions)
    assert any("correction cost" in r for r in s.revisions)


def test_malformed_value_is_invalid_not_missing():
    # A present-but-uncoercible value must be distinguished from an absent one.
    e = get_parser("claude").parse({"id": "x", "tokens": "not-a-number", "billing_cost": 0.1})
    assert e.field_status["tokens"] == FieldStatus.INVALID
    assert e.tokens == 0  # safe default, but provenance tells the truth
    assert e.silent_risk() is True


def test_unresolved_identity_is_quarantined_not_guessed():
    store = EventStore()
    no_id = get_parser("openai").parse({"model": "gpt-4", "tokens": 10})  # request_id missing
    result = store.ingest(no_id, delivery_id="d1")
    assert result is None
    assert store.quarantined_unidentified == 1
    assert len(store) == 0

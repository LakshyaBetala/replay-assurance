"""replay-check: the oracle-free monitor on a user's own stream."""

import json

from parser_assurance.cli import check_stream, main


def _claude(i, relocated=False):
    payload = {"id": f"m{i}", "model": "claude", "billing_cost": 0.01, "timestamp": "t"}
    if relocated:
        payload["usage"] = {"total_tokens": 100 + i}
    else:
        payload["tokens"] = 100 + i
    return json.dumps({"provider": "claude", "payload": payload})


def test_clean_stream_has_no_alarm():
    result = check_stream(_claude(i) for i in range(50))
    assert result["events"] == {"claude": 50}
    assert result["alarms"] == []


def test_relocated_field_raises_alarm_without_an_oracle():
    stream = [_claude(i) for i in range(30)] + [_claude(i, relocated=True) for i in range(30)]
    result = check_stream(stream)
    assert [(a["provider"], a["field"]) for a in result["alarms"]] == [("claude", "tokens")]
    assert result["defaulted_rate"]["claude"]["tokens"] == 50.0


def test_bad_input_is_counted_not_fatal():
    stream = ["not json", json.dumps({"provider": "nope", "payload": {}}), "[1, 2]"] + \
             [_claude(i) for i in range(25)]
    result = check_stream(stream)
    assert result["malformed_lines"] == 2
    assert result["unknown_providers"] == {"nope": 1}
    assert result["events"] == {"claude": 25}


def test_exit_codes(tmp_path, capsys):
    clean = tmp_path / "clean.jsonl"
    clean.write_text("\n".join(_claude(i) for i in range(50)))
    drift = tmp_path / "drift.jsonl"
    drift.write_text("\n".join([_claude(i) for i in range(30)] +
                               [_claude(i, relocated=True) for i in range(30)]))
    assert main([str(clean)]) == 0
    assert main([str(drift)]) == 1
    assert main([str(tmp_path / "missing.jsonl")]) == 2
    assert main([str(clean), "--provider", "unknown"]) == 2
    capsys.readouterr()
    assert main([str(drift), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["alarms"][0]["field"] == "tokens"

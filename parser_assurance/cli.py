"""`replay-check`: run the oracle-free Monitor over your own payload stream.

Input is JSON Lines, one raw provider payload per line, oldest first:

    {"provider": "claude", "payload": {"id": "...", "tokens": 512, ...}}

or, with --provider, each line is the bare payload itself.

Exit codes (so CI or a cron job can gate on it):
    0  no drift alarm
    1  at least one money field shows a probable schema change
    2  the input could not be read (missing file, nothing parseable)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, TextIO

from .canonical import MONEY_FIELDS, FieldStatus
from .parser import get_parser
from .replay import Monitor

_NON_REAL = (FieldStatus.MISSING, FieldStatus.NULL, FieldStatus.INVALID)


def check_stream(lines: Iterable[str], provider: Optional[str] = None,
                 monitor: Optional[Monitor] = None) -> Dict[str, Any]:
    """Feed a JSONL stream through the Day-1 parsers and the Monitor.

    Bad input never raises: malformed lines and unknown providers are counted
    and reported, so one corrupt record cannot hide drift in the rest.
    """
    monitor = monitor or Monitor()
    seen: Counter = Counter()
    non_real: Dict[str, Counter] = {}
    malformed, unknown = 0, Counter()

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if provider:
            prov, payload = provider, record
        elif isinstance(record, dict) and isinstance(record.get("payload"), dict):
            prov, payload = str(record.get("provider", "")), record["payload"]
        else:
            malformed += 1
            continue
        if not isinstance(payload, dict):
            malformed += 1
            continue
        parser = get_parser(prov)
        if parser is None:
            unknown[prov] += 1
            continue
        event = parser.parse(payload)
        monitor.observe(parser.provider, event.field_status)
        seen[parser.provider] += 1
        counts = non_real.setdefault(parser.provider, Counter())
        for f in MONEY_FIELDS:
            if event.field_status.get(f) in _NON_REAL:
                counts[f] += 1

    return {
        "events": dict(seen),
        "defaulted_rate": {
            p: {f: round(100 * non_real[p][f] / n, 1) for f in MONEY_FIELDS}
            for p, n in seen.items()
        },
        "alarms": monitor.alarms(),
        "malformed_lines": malformed,
        "unknown_providers": dict(unknown),
    }


def _print_text(result: Dict[str, Any], out: TextIO) -> None:
    print("replay-check: oracle-free drift monitor", file=out)
    for prov, n in sorted(result["events"].items()):
        rates = result["defaulted_rate"][prov]
        rate_txt = "  ".join(f"{f} defaulted {r:5.1f}%" for f, r in rates.items())
        print(f"  {prov:<11} {n:>6} events   {rate_txt}", file=out)
    if result["malformed_lines"]:
        print(f"  skipped {result['malformed_lines']} malformed line(s)", file=out)
    for prov, n in result["unknown_providers"].items():
        print(f"  skipped {n} line(s) for unknown provider {prov!r}", file=out)
    if result["alarms"]:
        for a in result["alarms"]:
            print(f"[ALARM] {a['signal']}", file=out)
    else:
        print("No drift alarms.", file=out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="replay-check",
        description="Detect silent payload-schema drift on a JSONL stream, no golden corpus needed.")
    ap.add_argument("stream", help="JSONL file, oldest first ('-' for stdin)")
    ap.add_argument("--provider", help="treat every line as a bare payload for this provider")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    ap.add_argument("--min-events", type=int, default=20, help="events needed before judging a field (default 20)")
    ap.add_argument("--jump", type=float, default=0.25, help="rise in defaulted-rate that counts as drift (default 0.25)")
    ap.add_argument("--min-recent", type=float, default=0.3, help="minimum recent defaulted-rate to alarm (default 0.3)")
    ap.add_argument("--window", type=float, default=0.4, help="fraction of events in each comparison window (default 0.4)")
    args = ap.parse_args(argv)

    if args.provider and get_parser(args.provider) is None:
        print(f"replay-check: unknown provider {args.provider!r}", file=sys.stderr)
        return 2
    monitor = Monitor(min_events=args.min_events, window_frac=args.window,
                      jump=args.jump, min_recent=args.min_recent)
    try:
        if args.stream == "-":
            result = check_stream(sys.stdin, args.provider, monitor)
        else:
            with open(args.stream, encoding="utf-8") as fh:
                result = check_stream(fh, args.provider, monitor)
    except OSError as exc:
        print(f"replay-check: cannot read {args.stream}: {exc}", file=sys.stderr)
        return 2

    if not result["events"]:
        print("replay-check: no parseable events in the stream", file=sys.stderr)
        return 2
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
    else:
        _print_text(result, sys.stdout)
    return 1 if result["alarms"] else 0


if __name__ == "__main__":
    sys.exit(main())

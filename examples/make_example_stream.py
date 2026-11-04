"""Regenerate examples/claude_drift.jsonl (deterministic).

60 Claude usage events, oldest first. The first 30 use the shape the Day-1
parser was written for (flat `tokens`); from event 31 the provider has moved
it to `usage.total_tokens`. Nothing in the stream says so -- replay-check has
to notice on its own.
"""

import json
import random
from pathlib import Path

random.seed(11)
out = Path(__file__).with_name("claude_drift.jsonl")
lines = []
for i in range(60):
    tokens = random.randint(200, 4000)
    payload = {"id": f"msg_{i:03d}", "model": "claude-sonnet",
               "billing_cost": round(tokens * 3e-6, 6),
               "timestamp": f"2026-10-{1 + i // 2:02d}T09:00:00Z"}
    if i < 30:
        payload["tokens"] = tokens
    else:
        payload["usage"] = {"total_tokens": tokens}
    lines.append(json.dumps({"provider": "claude", "payload": payload}))
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote {len(lines)} events to {out}")

"""Business Impact Mapping Engine.

Translates a structural drift into the downstream surface it corrupts. The point
is to make a schema change legible to a non-engineer: a field moving is abstract,
"this silently zeroed Cost Reconstruction on every dashboard" is not.
"""

from typing import List, Dict

# Semantic terminal keys -> the business surface they feed. Includes the real
# token-accounting shapes providers actually ship (input/output split, nested
# usage metadata), not just the synthetic ones.
IMPACT_MAP = {
    # usage / token accounting
    "tokens": "Usage Reporting",
    "total_tokens": "Usage Reporting",
    "totalTokenCount": "Usage Reporting",
    "tokenCount": "Usage Reporting",
    "input_tokens": "Usage Reporting",
    "output_tokens": "Usage Reporting",
    "prompt_tokens": "Usage Reporting",
    "completion_tokens": "Usage Reporting",
    "promptTokenCount": "Usage Reporting",
    "candidatesTokenCount": "Usage Reporting",
    # cost
    "cost": "Cost Reconstruction",
    "billing_cost": "Cost Reconstruction",
    # attribution
    "model": "Model Attribution",
    "id": "Event Identity / Dedup",
    "request_id": "Event Identity / Dedup",
    "uuid": "Event Identity / Dedup",
}


class ImpactMapper:
    def map_impact(self, drifts: List[Dict]) -> List[str]:
        impacts = set()
        for drift in drifts:
            terminal_key = None
            if "path" in drift:
                terminal_key = drift["path"].split(".")[-1]
            elif "old_path" in drift:
                # Use the old path: it carries the known semantic term.
                terminal_key = drift["old_path"].split(".")[-1]

            # Strip list indices like usage[0] -> usage
            if terminal_key:
                terminal_key = terminal_key.split("[")[0]

            if terminal_key and terminal_key in IMPACT_MAP:
                impacts.add(IMPACT_MAP[terminal_key])

        return sorted(list(impacts))

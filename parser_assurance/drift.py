"""Structural drift detection.

Compares two payload shapes and classifies every change:
  - Field Added / Field Removed
  - Type Mutation
  - Field Relocated  (the field moved to a new path)

Relocation is the dangerous one and the hard one, because a naive parser keeps
running and silently reads the default. We rank relocation evidence instead of
guessing:

  High   - a removed leaf and an added leaf carry the SAME value and type.
           (tokens=420 disappears, usage.total_tokens=420 appears -> the value
           moved, regardless of the key being renamed.)
  Medium - same terminal key name and type at a different path.

No regex, no ML -- just evidence we can defend.
"""

from typing import Any, Dict, List, Tuple


def flatten(payload: Any, prefix: str = "") -> Dict[str, Tuple[str, Any]]:
    """Flatten into {path: (type_name, value)} for every leaf."""
    out: Dict[str, Tuple[str, Any]] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else key
            out.update(flatten(value, path))
    elif isinstance(payload, list):
        for i, item in enumerate(payload):
            out.update(flatten(item, f"{prefix}[{i}]"))
    else:
        if prefix:
            out[prefix] = (type(payload).__name__, payload)
    return out


# Values too generic to be evidence of a move (a bare 0 or "" appearing in two
# places is coincidence, not relocation).
def _is_distinctive(value: Any) -> bool:
    if value in (None, 0, 0.0, "", False, True):
        return False
    return True


class DriftDetector:
    def detect(self, baseline_payload: Dict[str, Any],
               incoming_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        old = flatten(baseline_payload)
        new = flatten(incoming_payload)

        drifts: List[Dict[str, Any]] = []
        removed: List[str] = []
        added: List[str] = []

        for path, (typ, _val) in old.items():
            if path not in new:
                removed.append(path)
            elif new[path][0] != typ:
                drifts.append({
                    "type": "Type Mutation", "path": path,
                    "old_type": typ, "new_type": new[path][0],
                })

        for path in new:
            if path not in old:
                added.append(path)

        matched_old, matched_new = set(), set()

        # Pass 1: value-matched relocation (highest confidence).
        for r in removed:
            if r in matched_old:
                continue
            r_type, r_val = old[r]
            if not _is_distinctive(r_val):
                continue
            for a in added:
                if a in matched_new:
                    continue
                a_type, a_val = new[a]
                if a_type == r_type and a_val == r_val:
                    drifts.append({
                        "type": "Field Relocated", "old_path": r, "new_path": a,
                        "confidence": "High",
                        "reason": "Same value and type at a new path",
                    })
                    matched_old.add(r)
                    matched_new.add(a)
                    break

        # Pass 2: terminal-key relocation (medium confidence).
        for r in removed:
            if r in matched_old:
                continue
            r_type = old[r][0]
            r_terminal = r.split(".")[-1]
            for a in added:
                if a in matched_new:
                    continue
                if a.split(".")[-1] == r_terminal and new[a][0] == r_type:
                    drifts.append({
                        "type": "Field Relocated", "old_path": r, "new_path": a,
                        "confidence": "Medium",
                        "reason": "Same terminal key and type at a new path",
                    })
                    matched_old.add(r)
                    matched_new.add(a)
                    break

        for r in removed:
            if r not in matched_old:
                drifts.append({"type": "Field Removed", "path": r})
        for a in added:
            if a not in matched_new:
                drifts.append({"type": "Field Added", "path": a})

        return drifts


# Backwards-compatible alias
def flatten_schema(payload: Any, prefix: str = "") -> Dict[str, str]:
    return {p: t for p, (t, _v) in flatten(payload, prefix).items()}

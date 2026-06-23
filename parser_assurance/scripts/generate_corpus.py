"""Generate Realistic Golden Corpus for Replay Validation."""

import os
import json
import random
import uuid
from datetime import datetime, timezone, timedelta

def generate_payload(provider: str, version: str, i: int):
    tokens = random.randint(10, 1000)
    cost = tokens * 0.0001
    # Spread timestamps across a 30-day window for temporal realism
    base_time = datetime(2026, 5, 24, tzinfo=timezone.utc)
    offset = timedelta(days=random.uniform(0, 30), hours=random.uniform(0, 24))
    timestamp = (base_time + offset).isoformat()
    event_id = str(uuid.uuid4())
    
    payload = {}
    expected = {
        "event_id": event_id,
        "provider": provider,
        "model": "unknown",
        "tokens": tokens,
        "cost": cost,
        "timestamp": timestamp
    }
    
    if provider == "claude":
        model = "claude-3-opus"
        expected["model"] = model
        
        if version == "v1":
            payload = {
                "id": event_id,
                "model": model,
                "tokens": tokens,
                "billing_cost": cost,
                "timestamp": timestamp
            }
        elif version == "v2":
            # Partial Rollout (10% still uses v1 format)
            if random.random() < 0.10:
                payload = {"id": event_id, "model": model, "tokens": tokens, "billing_cost": cost, "timestamp": timestamp}
            else:
                payload = {"id": event_id, "model": model, "usage": {"total_tokens": tokens}, "billing_cost": cost, "timestamp": timestamp}
        elif version == "v3":
            # Delayed billing: payload born without cost, but expected retains the true cost.
            # This tests whether the system detects the missing data as a correctness failure.
            payload = {"id": event_id, "model": model, "usage": {"total_tokens": tokens}, "timestamp": timestamp}
            
    elif provider == "openai":
        model = "gpt-4"
        expected["model"] = model
        if version == "v1":
            payload = {"request_id": event_id, "model": model, "tokens": tokens, "cost": cost, "created": timestamp}
        elif version == "v2":
            # Type Mutation: tokens is a string!
            payload = {"request_id": event_id, "model": model, "tokens": str(tokens), "cost": cost, "created": timestamp}
            
    elif provider == "cursor":
        model = "claude-3.5-sonnet"
        expected["model"] = model
        if version == "v1":
            payload = {"uuid": event_id, "model": model, "tokens": tokens, "cost": cost, "timestamp": timestamp}
        elif version == "v2":
            # Optional fields added
            payload = {"uuid": event_id, "model": model, "tokens": tokens, "cost": cost, "timestamp": timestamp, "optional_cache_hits": 42}
            
    elif provider == "perplexity":
        model = "llama-3"
        expected["model"] = model
        if version == "v1":
            payload = {"id": event_id, "model": model, "tokens": tokens, "cost": cost, "timestamp": timestamp}
        elif version == "v2":
            # Field removed entirely (e.g. cost). Expected retains the true cost.
            # Parser will return 0.0 via .get() default. Replay should catch this.
            payload = {"id": event_id, "model": model, "tokens": tokens, "timestamp": timestamp}
            
    elif provider == "gemini":
        model = "gemini-1.5-pro"
        expected["model"] = model
        if version == "v1":
            payload = {"id": event_id, "model": model, "tokenCount": tokens, "cost": cost, "time": timestamp}
        elif version == "v2":
            payload = {"id": event_id, "model": model, "metadata": {"tokenCount": tokens}, "cost": cost, "time": timestamp}

    return payload, expected


def main():
    # Deterministic corpus: identical numbers on every machine and every run.
    random.seed(42)
    base_dir = os.path.join(os.path.dirname(__file__), "..", "data", "corpus")
    # Clean only the SYNTHETIC version dirs; never touch committed real fixtures (v_real/).
    import shutil
    if os.path.exists(base_dir):
        for prov in os.listdir(base_dir):
            for version in os.listdir(os.path.join(base_dir, prov)):
                if version == "v_real":
                    continue
                shutil.rmtree(os.path.join(base_dir, prov, version))
    os.makedirs(base_dir, exist_ok=True)
    
    distributions = {
        "claude": {"count": 400, "versions": ["v1", "v2", "v3"]},
        "openai": {"count": 300, "versions": ["v1", "v2"]},
        "cursor": {"count": 150, "versions": ["v1", "v2"]},
        "perplexity": {"count": 80, "versions": ["v1", "v2"]},
        "gemini": {"count": 70, "versions": ["v1", "v2"]}
    }
    
    total = 0
    for prov, info in distributions.items():
        count = info["count"]
        versions = info["versions"]
        count_per_version = count // len(versions)
        
        for v_idx, version in enumerate(versions):
            v_dir = os.path.join(base_dir, prov, version)
            os.makedirs(v_dir, exist_ok=True)
            
            # If it's the last version, give it any remainder payloads
            payloads_to_gen = count_per_version
            if v_idx == len(versions) - 1:
                payloads_to_gen += count % len(versions)
                
            for i in range(1, payloads_to_gen + 1):
                payload, expected = generate_payload(prov, version, i)
                
                with open(os.path.join(v_dir, f"payload_{i:03d}.json"), "w") as f:
                    json.dump(payload, f, indent=2)
                with open(os.path.join(v_dir, f"expected_{i:03d}.json"), "w") as f:
                    json.dump(expected, f, indent=2)
                    
                total += 1

    print(f"Generated {total} realistic golden corpus payloads in {base_dir}")

if __name__ == "__main__":
    main()

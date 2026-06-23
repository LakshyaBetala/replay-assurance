# Corpus Provenance

**HONESTY DECLARATION**

The 1000+ payloads in the `corpus/` directory are **synthetic**. 

They are generated from representative public schemas based on provider documentation (Claude, OpenAI, Gemini, Cursor, Perplexity). They are **not** captured production traffic.

The drift simulations (e.g., relocating `tokens` to `usage.total_tokens` or omitting the `cost` field) are intentionally designed mutations meant to simulate realistic schema evolution over time.

While the *mechanics* of the drift are synthetic, the *failure mode* they test (silent canonical degradation despite execution success) is a genuine architectural vulnerability in ingestion pipelines that rely purely on schema validation or exception monitoring.

To complement this synthetic corpus, we also test against a smaller set of **Real Payload Fixtures** (committed under `corpus/<provider>/v_real/`) sourced directly from public provider documentation. These use authentic response shapes the generator never produces — e.g. Claude's `usage.input_tokens`/`output_tokens` split and Gemini's `usageMetadata.totalTokenCount` — so they are the honest test of whether the harness catches a drift that was *not* scripted. The OpenAI real fixture is the sole reason OpenAI scores 99.7% rather than 100%.

**Reproducibility:** the synthetic corpus is seeded (`random.seed(42)`), so every metric in the README is identical on every machine and every run.

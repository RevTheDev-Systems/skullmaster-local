# Performance

Reproducible local benchmarks, not vibes. Run and record:

```bash
uv run python -m app.benchmarks --sizes small,medium --json evals/benchmarks-baseline.json
uv run python -m app.benchmarks --sizes small --generate   # adds TTFT / throughput
```

The harness uses an isolated temp data dir and measures startup, model
discovery (cold/warm + provider calls), embedding throughput, ingestion,
retrieval latency, memory, and optionally generation TTFT/throughput. Every
report records the environment (platform, Python, CPU count, chat/embed model),
so results are comparable over time. Optimize only what the numbers show.

## Committed baseline

`evals/benchmarks-baseline.json` — macOS arm64, Python 3.12.13, 18 CPUs,
`qwen3:30b` + `nomic-embed-text` (768-dim):

| Metric | Value |
|---|---|
| Startup (`import app.main`) | 0.82 s |
| Model discovery | cold 0.011 s (4 `show`) → warm 0.003 s |
| Embedding throughput | ~50 texts/s |
| Ingestion (20 chunks) | 0.48 s (~42 chunks/s) |
| Ingestion (200 chunks) | 3.43 s (~58 chunks/s) |
| Retrieval (small notebook) | 21 ms mean |
| Retrieval (medium notebook) | 44 ms mean |
| Peak memory | 171 MB |

## Not automated here

Audio Overview generation, Whisper transcription, and full LLM generation are
long-running and model-bound; `--generate` covers LLM TTFT/throughput on demand,
and the others are exercised by the acceptance journey rather than timed in CI.
The benchmark corpus is synthetic and deterministic; the RAG *quality* baseline
lives in `evals/baseline.json` (Phase 5).

## Observed bottleneck candidates (not yet tuned)

- Retrieval rebuilds the BM25 index over a notebook's rows on every query; fine
  at these sizes, worth revisiting for very large notebooks.
- Multi-document Recall@3 (Phase 5) is the first quality gap to investigate.

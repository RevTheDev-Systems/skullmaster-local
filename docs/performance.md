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

## Tuning outcome (retrieval)

The Phase 5 baseline flagged the multi-document case (recall@3 0.944). A measured
sweep of reciprocal-rank-fusion weights showed BM25 weighted slightly above the
vector signal fixes it: **recall@3 0.944 → 1.0** with no change to recall@1
(where a multi-document question can only put one source first), recall@8, MRR,
or any generation metric. `store.RRF_BM25_WEIGHT = 1.5` is now the default, and
`evals/baseline.json` was regenerated.

## Chunk-size / overlap sweep

`uv run python -m app.evaluation --sweep` re-chunks the corpus at sizes
1600/3200/6400 with overlaps 0/200/400 and reports retrieval metrics
(`evals/chunk-sweep.json`). All nine configurations score identically
(recall@1 0.926, recall@3/@8 1.0, MRR 1.0, page accuracy 1.0): retrieval is
**saturated** on this corpus, so no chunking change is justified. The configured
3200/400 stays. Re-run the sweep whenever the corpus is expanded.

## Observed bottleneck candidates (not yet tuned)

- Retrieval rebuilds the BM25 index over a notebook's rows on every query; fine
  at these sizes, worth revisiting for very large notebooks.
- Reranking / query expansion remain unexplored — re-evaluate only if a larger
  corpus shows headroom.

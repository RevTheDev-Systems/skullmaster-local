"""Reproducible local performance benchmarks.

Measures the parts that matter for this app — startup, model discovery,
embedding throughput, ingestion, retrieval latency, memory, and (optionally)
generation TTFT / throughput — across small/medium/large synthetic notebooks,
and records the environment (hardware, models) alongside every result so numbers
are comparable over time. Optimize only what the numbers show.

The module imports only the standard library at import time; app modules are
imported lazily in `main()` after pointing the run at an isolated data dir.

Usage:
    python -m app.benchmarks --json evals/benchmarks-baseline.json
    python -m app.benchmarks --sizes small,medium --generate
"""

from __future__ import annotations

import json
import platform
import time
from pathlib import Path
from typing import Any

SIZES = {"small": 20, "medium": 200, "large": 1000}  # target chunks per notebook
_SENTENCE = (
    "The Meridian Array in the Atacama Desert produces 1.2 gigawatts and "
    "powers roughly 800,000 homes at a cost of 940 million dollars. "
)


def synthetic_text(chunks: int) -> str:
    """~CHUNK_CHARS of text per target chunk, separated by blank lines."""
    per_chunk = 3200
    return "\n\n".join(_SENTENCE * (per_chunk // len(_SENTENCE) or 1) for _ in range(chunks))


def _mean(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _time(fn, *args, **kwargs) -> tuple[float, Any]:
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    return time.perf_counter() - started, result


def _memory_mb() -> float:
    import resource

    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports kilobytes.
    return round(usage / (1024 * 1024) if platform.system() == "Darwin" else usage / 1024, 1)


def environment(chat_model: str = "", embed_model: str = "") -> dict:
    import os

    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "chat_model": chat_model,
        "embed_model": embed_model,
    }


def measure_startup() -> dict:
    """Time a cold `import app.main` in a fresh interpreter."""
    import subprocess
    import sys

    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - started
    return {"seconds": round(elapsed, 3), "ok": proc.returncode == 0}


def measure_model_discovery(llm) -> dict:
    cold_s, models = _time(llm.list_models, refresh=True)
    warm_s, _ = _time(llm.list_models)
    ollama = getattr(llm, "ollama", None)
    return {
        "models": len(models),
        "cold_seconds": round(cold_s, 4),
        "warm_seconds": round(warm_s, 4),
        "show_calls": getattr(ollama, "metadata_calls", None),
        "list_calls": getattr(ollama, "list_calls", None),
    }


def measure_embedding(llm, *, count: int = 32) -> dict:
    texts = [_SENTENCE for _ in range(count)]
    elapsed, vectors = _time(llm.embed, texts)
    return {
        "texts": count,
        "seconds": round(elapsed, 4),
        "texts_per_second": round(count / elapsed, 2) if elapsed else None,
        "dim": len(vectors[0]) if vectors else None,
    }


def measure_ingestion(llm, store_mod, db_mod, *, chunks: int, notebook_name: str) -> dict:
    from .chunker import chunk_segments

    segments: list[tuple[int | None, str]] = [(None, synthetic_text(chunks))]
    chunk_list = chunk_segments(segments)
    notebook = db_mod.create_notebook(notebook_name)
    nb_id = notebook["id"]
    source = db_mod.create_source(
        nb_id, "bench.txt", "text", "bench.txt", None, len(chunk_list), status="ready"
    )
    elapsed, _ = _time(store_mod.add_chunks, nb_id, source["id"], "bench.txt", chunk_list, llm=llm)
    return {
        "notebook_id": nb_id,
        "chunk_count": len(chunk_list),
        "seconds": round(elapsed, 4),
        "chunks_per_second": round(len(chunk_list) / elapsed, 2) if elapsed else None,
    }


def measure_retrieval(llm, store_mod, nb_id: str, *, queries: int = 5) -> dict:
    question = "How much did the Meridian Array cost and how many homes does it power?"
    latencies = []
    for _ in range(queries):
        elapsed, hits = _time(store_mod.hybrid_search, nb_id, question, k=8, llm=llm)
        latencies.append(elapsed)
        assert hits
    ordered = sorted(latencies)
    return {
        "queries": queries,
        "mean_seconds": round(_mean(latencies) or 0, 4),
        "p50_seconds": round(ordered[len(ordered) // 2], 4),
        "max_seconds": round(ordered[-1], 4),
    }


def measure_generation(llm, *, prompt: str = "Reply with one short sentence about arrays.") -> dict:
    messages = [{"role": "user", "content": prompt}]
    started = time.perf_counter()
    first = None
    chars = 0
    for token in llm.chat(messages, stream=True):
        if first is None:
            first = time.perf_counter() - started
        chars += len(token)
    total = time.perf_counter() - started
    return {
        "ttft_seconds": round(first, 4) if first else None,
        "total_seconds": round(total, 3),
        "chars": chars,
        "chars_per_second": round(chars / total, 2) if total else None,
    }


def run(sizes=("small",), *, generate: bool = False, llm=None, store_mod=None, db_mod=None) -> dict:
    if store_mod is None:
        from . import store as store_mod
    if db_mod is None:
        from . import db as db_mod
    if llm is None:
        from .providers import get_llm

        llm = get_llm()

    db_mod.init_db()
    status = llm.status()
    report: dict = {
        "environment": environment(status.get("chat_model", ""), status.get("embed_model", "")),
        "startup": measure_startup(),
        "model_discovery": measure_model_discovery(llm),
        "embedding": measure_embedding(llm),
        "notebooks": {},
        "peak_memory_mb": None,
    }

    notebooks = []
    try:
        for size in sizes:
            target = SIZES.get(size, SIZES["small"])
            ingest = measure_ingestion(
                llm, store_mod, db_mod, chunks=target, notebook_name=f"bench::{size}"
            )
            notebooks.append(ingest["notebook_id"])
            report["notebooks"][size] = {
                "ingestion": ingest,
                "retrieval": measure_retrieval(llm, store_mod, ingest["notebook_id"]),
            }
        if generate:
            report["generation"] = measure_generation(llm)
    finally:
        for nb_id in notebooks:
            store_mod.delete_notebook_chunks(nb_id)
            db_mod.delete_notebook(nb_id)

    report["peak_memory_mb"] = _memory_mb()
    return report


def _print(report: dict) -> None:
    env = report["environment"]
    print(
        f"\nEnvironment: {env['platform']} | Python {env['python']} | "
        f"{env['cpu_count']} CPUs | chat={env['chat_model']} embed={env['embed_model']}"
    )
    s = report["startup"]
    print(f"\nStartup: import app.main = {s['seconds']}s (ok={s['ok']})")
    d = report["model_discovery"]
    print(
        f"Model discovery: {d['models']} models | cold {d['cold_seconds']}s "
        f"({d['show_calls']} show calls) | warm {d['warm_seconds']}s"
    )
    e = report["embedding"]
    print(
        f"Embedding: {e['texts']} texts in {e['seconds']}s "
        f"= {e['texts_per_second']}/s (dim {e['dim']})"
    )
    for size, data in report["notebooks"].items():
        ing, ret = data["ingestion"], data["retrieval"]
        print(
            f"\n[{size}] {ing['chunk_count']} chunks | ingest {ing['seconds']}s "
            f"({ing['chunks_per_second']}/s)"
        )
        print(
            f"[{size}] retrieval mean {ret['mean_seconds']}s | "
            f"p50 {ret['p50_seconds']}s | max {ret['max_seconds']}s"
        )
    if "generation" in report:
        g = report["generation"]
        print(
            f"\nGeneration: TTFT {g['ttft_seconds']}s | "
            f"{g['chars_per_second']} chars/s over {g['total_seconds']}s"
        )
    print(f"\nPeak memory: {report['peak_memory_mb']} MB")


def main(argv=None) -> int:
    import argparse
    import os
    import tempfile

    parser = argparse.ArgumentParser(prog="python -m app.benchmarks", description=__doc__)
    parser.add_argument("--sizes", default="small", help="comma-separated: small,medium,large")
    parser.add_argument(
        "--generate", action="store_true", help="also measure generation TTFT/throughput (slow)"
    )
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args(argv)

    os.environ["NLM_DATA_DIR"] = args.data_dir or tempfile.mkdtemp(prefix="skullmaster-bench-")
    sizes = tuple(s.strip() for s in args.sizes.split(",") if s.strip())

    from . import db, store
    from .providers import get_llm

    report = run(sizes, generate=args.generate, llm=get_llm(), store_mod=store, db_mod=db)
    _print(report)
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

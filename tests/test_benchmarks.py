"""Performance benchmark harness smoke test (deterministic stub)."""

import pytest


class _StubLLM:
    def __init__(self):
        self.chat_model = "stub"
        self.embed_model = "stub-embed"

    def embed(self, texts):
        return [[float(len(t) % 7)] * 4 for t in texts]

    def list_models(self, refresh=False):
        return [{"name": "stub"}]

    def status(self):
        return {"chat_model": "stub", "embed_model": "stub-embed"}

    def chat(self, messages, stream=False):
        return iter(["hello "]) if stream else "hello"


@pytest.fixture()
def stub_store(tmp_path, monkeypatch):
    import lancedb

    from app import store

    monkeypatch.setattr(store, "_db", lancedb.connect(str(tmp_path / "lancedb")))
    return store


def test_benchmark_report_structure(stub_store):
    from app import benchmarks, db

    report = benchmarks.run(
        ("small",), generate=True, llm=_StubLLM(), store_mod=stub_store, db_mod=db
    )

    assert report["model_discovery"]["models"] == 1
    assert report["embedding"]["texts_per_second"] is not None
    assert report["notebooks"]["small"]["retrieval"]["queries"] == 5
    assert report["peak_memory_mb"] > 0
    assert report["generation"]["chars"] > 0
    assert report["environment"]["python"]

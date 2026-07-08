"""Narrow unit tests: chunking, citation validation, filename/URL safety."""
import pytest

from app import ingest
from app.chunker import chunk_segments
from app.config import CHUNK_CHARS, CHUNK_OVERLAP
from app.main import _sanitize_filename
from app.rag import strip_invalid_citations, strip_think
from app.store import _chunk_id


def test_chunker_deterministic():
    segments = [(1, "para one.\n\npara two.\n\n" + "x" * 5000)]
    a, b = chunk_segments(segments), chunk_segments(segments)
    assert a == b
    # a chunk may carry CHUNK_OVERLAP chars from its predecessor
    assert all(len(c["text"]) <= CHUNK_CHARS + CHUNK_OVERLAP + 2 for c in a)
    assert [c["seq"] for c in a] == list(range(len(a)))
    assert all(c["page"] == 1 for c in a)


def test_chunk_ids_stable_and_distinct():
    assert _chunk_id("src", 0, "hello") == _chunk_id("src", 0, "hello")
    assert _chunk_id("src", 0, "hello") != _chunk_id("src", 1, "hello")
    assert _chunk_id("src", 0, "hello") != _chunk_id("other", 0, "hello")


def test_strip_invalid_citations():
    assert strip_invalid_citations("ok [1] bad [7] end [2]", 2) == "ok [1] bad  end [2]"
    assert strip_invalid_citations("none [3]", 0) == "none "


def test_strip_think():
    assert strip_think("<think>secret</think>answer") == "answer"
    assert strip_think("leaked</think>answer") == "answer"
    assert strip_think("plain") == "plain"


def test_sanitize_filename():
    assert _sanitize_filename("../../etc/passwd") == "passwd"
    assert "/" not in _sanitize_filename("a/b\\c<d>.txt")
    assert _sanitize_filename("...") == "upload"
    assert _sanitize_filename("report v2.pdf") == "report v2.pdf"


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "javascript:alert(1)",
    "not-a-url",
])
def test_url_scheme_rejected(url):
    with pytest.raises(ingest.IngestError):
        ingest.parse_url(url)

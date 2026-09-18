"""Local tool layer: arithmetic, dates, units, and strict validation."""

import pytest

from app import tools
from app.tools import ToolError

# ---------- calculator ----------


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("2 + 3 * 4", 14),
        ("(2 + 3) * 4", 20),
        ("2 ** 10", 1024),
        ("7 % 4", 3),
        ("-3 + 1", -2),
    ],
)
def test_calculator(expression, expected):
    assert tools.calculator(expression)["result"] == expected


@pytest.mark.parametrize(
    "bad",
    [
        "__import__('os').system('echo hi')",
        "open('/etc/passwd')",
        "x + 1",
        "1 if True else 2",
        "[1, 2]",
        "1 / 0",
        "9 ** 9 ** 9",
    ],
)
def test_calculator_refuses_unsafe_or_invalid(bad):
    with pytest.raises(ToolError):
        tools.calculator(bad)


# ---------- dates ----------


def test_days_between():
    assert tools.days_between("1969-07-20", "2026-09-18")["days"] > 0
    assert tools.days_between("2026-01-01", "2026-01-01")["days"] == 0


def test_days_between_rejects_bad_dates():
    with pytest.raises(ToolError):
        tools.days_between("yesterday", "today")


# ---------- units ----------


def test_convert_length_and_temperature():
    assert tools.convert(1, "km", "m")["result"] == pytest.approx(1000)
    assert tools.convert(100, "c", "f")["result"] == pytest.approx(212)
    assert tools.convert(32, "f", "c")["result"] == pytest.approx(0)


def test_convert_rejects_unknown_units():
    with pytest.raises(ToolError):
        tools.convert(1, "kg", "km")


# ---------- registry ----------


def test_run_tool_and_validation():
    assert tools.run_tool("word_count", {"text": "one two three"})["words"] == 3
    assert any(t["name"] == "calculator" for t in tools.list_tools())
    with pytest.raises(ToolError):
        tools.run_tool("nope", {})
    with pytest.raises(ToolError):
        tools.run_tool("calculator", {"wrong": "1+1"})


# ---------- API ----------


# ---------- wiring into the grounded research path ----------


class _FakeLLM:
    def __init__(self, plan=None):
        self.plan = plan
        self.calls = []

    def embed(self, texts):
        # 8-dim to match MockLLM, so it can search the shared test index.
        return [[0.0] * 8 for _ in texts]

    def chat(self, messages, stream=False):
        self.calls.append(stream)
        if stream:
            return iter(["It is 42 [1]."])
        return self.plan if self.plan is not None else "Direct answer."


def test_research_tool_round_runs_once():
    from app import db, rag

    db.init_db()
    llm = _FakeLLM(plan='{"tool": "calculator", "args": {"expression": "6*7"}}')
    _, tokens = rag.research_stream(["nb"], "What is 6*7?", [], llm=llm, use_tools=True)
    assert "".join(tokens) == "It is 42 [1]."
    assert llm.calls == [False, True]  # one planning call, then the answer


def test_research_without_tools_streams_directly():
    from app import db, rag

    db.init_db()
    llm = _FakeLLM()
    _, tokens = rag.research_stream(["nb"], "hi", [], llm=llm, use_tools=False)
    assert "".join(tokens) == "It is 42 [1]."
    assert llm.calls == [True]  # no planning call


def test_research_api_accepts_use_tools(client, notebook):
    res = client.post(
        "/api/research", json={"question": "What is 6*7?", "history": [], "use_tools": True}
    )
    assert res.status_code == 200, res.text
    assert "event: sources" in res.text and "event: done" in res.text


def test_tools_api(client):
    listing = client.get("/api/tools").json()["tools"]
    assert any(t["name"] == "calculator" for t in listing)

    ok = client.post("/api/tools/run", json={"name": "calculator", "args": {"expression": "6*7"}})
    assert ok.status_code == 200 and ok.json()["result"]["result"] == 42

    bad = client.post("/api/tools/run", json={"name": "calculator", "args": {"expression": "1/0"}})
    assert bad.status_code == 422

    unknown = client.post("/api/tools/run", json={"name": "nope", "args": {}})
    assert unknown.status_code == 422

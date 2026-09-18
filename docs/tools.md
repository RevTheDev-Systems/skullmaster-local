# Local tools (Phase 22)

The first piece of the post-v1 tool layer: small, deterministic, side-effect-free
utilities that research workflows can call. The design is **safe by
construction** — nothing is run through `eval`, and every tool validates its
arguments and raises `ToolError` on anything unexpected.

## Registry

| Tool | Args | Returns |
|---|---|---|
| `calculator` | `expression` | value — `+ - * / // % **` and parentheses only |
| `days_between` | `start`, `end` (ISO dates) | whole days |
| `convert` | `value`, `from_unit`, `to_unit` | converted value |
| `word_count` | `text` | words and characters |
| `count_in_sources` | `term` | occurrences across the notebook's sources |
| `find_in_sources` | `term`, `limit` | short matching passages + source/page |

The last two are **source-scoped** (`scope: "sources"`): they run against the
notebook's own text and are only available inside a research answer (the research
path injects a bounded `{sources: [...]}` context). Calling them through
`/api/tools/run` directly returns 422.

`convert` supports length (m/km/cm/mm/mi/ft/in/yd), mass (g/kg/mg/lb/oz), data
(b/kb/mb/gb/tb), and temperature (c/f/k).

## Safety properties

- **No code execution.** `calculator` parses with `ast` and walks only a
  whitelist of node types; names, calls, attribute access, comprehensions, and
  conditionals are rejected. Exponents above 1000 and results above 1e18 are
  refused, so there is no arithmetic DoS.
- **Strict arguments.** Unknown tool names, unexpected argument keys, and bad
  types raise `ToolError` (HTTP 422). `run_tool` never forwards arbitrary kwargs.
- **Bounded input.** Expressions and text are length-capped.
- **Auditable.** Every call returns its `name`, `args`, and `result`, so a
  workflow can record exactly what ran.

## API

```bash
GET  /api/tools                 # catalog: name, description, argument shapes
POST /api/tools/run             # {"name": "calculator", "args": {"expression": "6*7"}}
```

Both require a session. A refused or malformed call returns **422** with a clear
message.

## Wired into research answers

`POST /api/research` accepts `"use_tools": true` (the chat bar's **🧮 Tools**
toggle). When enabled, `rag.research_stream` allows **one bounded tool round**:

1. The grounded system prompt gains a strict protocol: reply with *only*
   `{"tool": "<name>", "args": { ... }}` when a tool is needed, else answer
   normally.
2. If a tool call is parsed, it is run through `run_tool`, and the call, args,
   and result are **logged** (`app.rag`); the trusted result is fed back with an
   explicit "do not cite it" instruction. Source-scoped tools receive a bounded
   `{sources: [...]}` context gathered from the target notebooks.
3. The final answer is then streamed with the usual grounded citations.

If the model answers directly (no tool), that text streams unchanged. Grounding,
citation validation, and the primary chat path are untouched — tools are strictly
opt-in.

## Roadmap

Source-scoped tools are delivered (`count_in_sources`, `find_in_sources`). A
deliberate future step is a stricter multi-step budget (currently exactly one
tool round), still local, deterministic, and cite-or-refuse.

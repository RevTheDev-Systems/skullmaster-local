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

## Roadmap

This is the substrate, not a full agent. Deliberate next steps: let the grounded
answer path optionally *request* a tool through a strict JSON protocol (bounded
and logged), and add source-scoped tools (e.g. count occurrences across a
notebook) — all still local, deterministic, and cite-or-refuse.

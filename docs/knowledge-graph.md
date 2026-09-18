# Knowledge graph (Mind Graph)

Mind Graph is the **visualization layer** of a source-grounded knowledge graph.

```
Sources ──▶ entity extraction (LLM) ──▶ evidence binding (server) ──▶ graph model ──▶ Mind Graph UI
```

- **Entity/relationship extraction** — the model produces a root, branches, and
  children, plus cross-links, strictly from the notebook's sources. It is
  instructed to reuse the exact names, numbers, and terms from the sources.
- **Evidence binding** — `studio.bind_evidence()` then binds each node label to
  its best-matching source chunk (verbatim match first, otherwise highest token
  overlap) and records the source name, page/second, and a snippet. A node with
  no supporting chunk is simply left unbound — evidence is never invented.
- **Graph model** — `spec["evidence"]` maps a node label to
  `{source, page, snippet}`.
- **UI** — the radial SVG is rendered as before, with an "Evidence" list beneath
  it so every node can be traced back to the passage it came from.

## Roadmap

A fuller graph representation (typed entities/relations, cross-notebook
research, navigable edges) builds on this step. Evidence binding is deliberately
deterministic and local, so the graph stays reproducible and source-grounded
rather than decorative.

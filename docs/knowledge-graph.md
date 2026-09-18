# Knowledge graph (Mind Graph)

Mind Graph is the **visualization layer** of a source-grounded knowledge graph.

```
Sources ──▶ entity extraction (LLM) ──▶ evidence binding (server) ──▶ graph model ──▶ Mind Graph UI
```

- **Entity/relationship extraction** — the model produces a root, branches, and
  children, plus cross-links, strictly from the notebook's sources. It is
  instructed to reuse the exact names, numbers, and terms from the sources.
- **Typed entities** — every node can be classified with a `node_types` entry
  from a controlled vocabulary (`concept`, `organization`, `person`, `location`,
  `date`, `metric`, `event`, `document`). Unknown labels or invalid types are
  dropped server-side.
- **Typed relations** — each cross-link carries a `type` from a controlled
  vocabulary (`related_to`, `part_of`, `causes`, `measures`, `located_at`,
  `enables`, `contradicts`, `precedes`); anything else normalizes to
  `related_to`. Endpoints that aren't real nodes prune the edge.
- **Evidence binding** — `studio.bind_evidence()` binds each node label to its
  best-matching source chunk (verbatim match first, otherwise highest token
  overlap), recording source, page/second, and a snippet. Each **edge inherits
  the evidence of one of its endpoints**, so relations are traceable too. A node
  with no supporting chunk is left unbound — evidence is never invented.
- **Graph model** — `spec["evidence"]` maps node label → `{source, page, snippet}`;
  `spec["node_types"]` maps node label → entity type; `spec["links"][i]` carries
  `{from, to, label, type, evidence}`.
- **UI** — the radial SVG colours nodes by entity type, styles edges by relation
  type (with the relation type labelled), and shows an entity/relation legend.
  An "Evidence" panel lists node sources and a "Relations" list with their
  evidence, so the graph is navigable and source-grounded rather than decorative.

## Roadmap

The next step is a dedicated graph explorer (pan/zoom, click-to-focus, and
cross-notebook graphs). Evidence binding is deliberately deterministic and
local, so the graph stays reproducible.

# Git history reconciliation

Audited before the stable release. History is **linear on `main`** and no
squashing was necessary — each commit is a bounded, reviewable unit with useful
provenance.

## Branches

| Ref | Purpose |
|---|---|
| `main` | Canonical line: prototype commits → stabilization Phases 1–18 → RC → stable |
| `baseline/pre-skullmaster-stabilization` | Rollback point at `882f7d4` (pre-stabilization) |
| `pre-merge-main` | Local safety ref from the remote-UI merge (`ade0d56`) |
| remote `origin/main` | Pushed through Phase 18 at time of writing; RC/stable follow at Phase 22 |

## Checks performed

- **No secrets or runtime data tracked** — `.env`, `data/`, `models/`, SQLite,
  WAVs, and `server.log` are all untracked/ignored; only `.env.example` is in
  the repo.
- **Unpushed commits reviewed** — every local commit is one of the program's
  phases (no stray/WIP commits, no accidental files). The untracked
  `SkullMaster_iQ_Complete_Project_Plan.pdf` is intentionally not committed.
- **Large blobs** — limited to app/brand icons under `assets/` and `static/`
  (~1–2 MB each); acceptable, no accidental binaries.
- **Divergence** — the earlier remote UI divergence was reconciled with a
  history-preserving merge (`ade0d56`), never a force-push.

## Intended final history

```
origin/main ──▶ Phases 8–18 ──▶ v1.1.0-rc.1 ──▶ v1.1.0 (tag) ──▶ push (Phase 22)
baseline/pre-skullmaster-stabilization ──▶ 882f7d4
```

# 0003 — Monorepo layout: backend / game / shared at the repo root

- **Status:** Accepted
- **Date:** 2026-06-22
- **Relates to:** `CLAUDE.md` (File placement, Architecture spine); `../npc-agent-service/v2/plan.md` §9; `../npc-agent-service/v2/implementation.md`

## Context

The product has two genuinely different components — a **Python backend** (the NPC Agent Service:
FastAPI + LangGraph + SQLite + Chroma) and a **Godot game client** (GDScript) — plus shared assets
(the `/talk` HTTP/WS contract and the lore JSON both sides consume).

Until now CLAUDE.md and the plan assumed the **repo root *is* the backend** (`app/`, `eval/`,
`tests/` at root). That mixes two unrelated stacks (different deps, tooling, `.gitignore`, CI) at one
level and gives the game nowhere to live. No code exists yet, so restructuring now costs nothing;
later it would mean moving files and rewriting every path reference.

## Decision

Use a **monorepo** with one top-level directory per component:

```
RPG-Game/
├── backend/          # NPC Agent Service — app/, eval/, tests/, pyproject.toml
├── game/             # Godot client — project.godot, scenes/, scripts/
├── shared/
│   ├── contracts/    # the /npc/{id}/talk request/response schema — single source of truth
│   └── lore/         # the LLM-generated lorebook (backend embeds it, game may display it)
├── docs/             # design docs — STAY at root, they span the whole system
├── CLAUDE.md
├── MEMORY.md
└── README.md
```

- **`backend/`** owns all Python code. The file-placement table in CLAUDE.md now nests under it
  (`backend/app/api/`, `backend/app/graph/`, …). Slice S0 creates the first file here.
- **`shared/contracts/`** is the seam between game and backend — the `/talk` JSON schema lives in
  exactly one owned place so the two sides cannot drift. Consistent with the spine rule: the contract
  is code-owned, not re-defined per consumer.
- **`docs/` stays at the root** because the plan, ADRs, and diagrams describe the *system*, not just
  the backend.

## Alternatives considered

- **Backend at root, add only `game/` + `shared/`** — less doc churn, but the backend isn't visually
  separated from the game and `app/` at root reads as "this repo is the backend." Rejected for
  clarity now that a second component exists.
- **Polyrepo (separate git repos sharing a contract package)** — more isolation, but for a solo
  portfolio piece it doubles clone/CI/version overhead and weakens the "here is the whole system at a
  glance" story reviewers want. Rejected.

## Consequences

- **+** Clean stack separation; each component gets its own deps/tooling/CI later.
- **+** The contract has one home, enforcing game↔backend agreement.
- **+** One repo = one coherent portfolio artifact.
- **−** One-time doc update: CLAUDE.md file-placement table + architecture spine, plan §9,
  `implementation.md` paths now point under `backend/`. No code to move (none exists yet).
- **Affected:** `CLAUDE.md`, `docs/npc-agent-service/v2/plan.md` §9,
  `docs/npc-agent-service/v2/implementation.md`, `MEMORY.md` (project-state snapshot).

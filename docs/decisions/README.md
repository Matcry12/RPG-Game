# Decisions Log (ADRs)

**Every decision gets recorded here** — one file per decision. This is the durable record of *what*
we chose and *why*, so choices survive across sessions and aren't silently re-litigated.

## Rules

- One decision = one file: `NNNN-short-title.md` (zero-padded, increasing). Newest number = newest decision.
- Write the ADR **at the moment the decision is made**, before moving on.
- Never edit a decided ADR's substance. To change course, write a **new** ADR that supersedes it
  (set the old one's status to `Superseded by NNNN` and link both).
- If a decision answers an open question from `../npc-agent-service/v2/plan.md` §10, also tick it off in `../../MEMORY.md`.
- Mistakes and things-to-avoid do **not** go here — they go in `../../MEMORY.md` (Mistakes & Lessons).

## Template

Copy `0000-template.md` for each new decision.

## Index

| # | Title | Status | Date |
|---|-------|--------|------|
| [0001](0001-groq-primary-brain-local-fallback.md) | Groq free tier as primary NPC brain, local Gemma 3n failover | Accepted | 2026-06-22 |
| [0002](0002-vertical-slice-build-approach.md) | Build in vertical slices (tracer bullets), not horizontal layers | Accepted | 2026-06-22 |
| [0003](0003-monorepo-layout.md) | Monorepo layout: backend / game / shared at the repo root | Accepted | 2026-06-22 |
| [0004](0004-rejection-feedback-via-persona-prompt.md) | Rejection feedback conditions the prose generation, not a second tool round-trip | Accepted | 2026-06-22 |
| [0005](0005-agentic-tool-loop-at-s4.md) | Adopt an agentic tool-calling loop at S4 (replace the linear two-call flow) | Accepted | 2026-06-22 |
| [0006](0006-episodic-memory-write-and-recall-policy.md) | Episodic memory: write policy, provisional importance, similarity-only recall | Accepted | 2026-06-23 |

# 0002 — Build in vertical slices (tracer bullets), not horizontal layers

- **Status:** Accepted
- **Date:** 2026-06-22
- **Relates to:** `../npc-agent-service/v2/plan.md` §8; `../npc-agent-service/v2/implementation.md` (S0–S11); `CLAUDE.md` (Planning — vertical slices)

## Context

The plan touches many layers (FastAPI, LangGraph, SQLite, Chroma, the gate, eval). There are two ways
to sequence the build:

- **Horizontal:** build each layer fully (all of SQLite, then all tools, then wire it up). Produces
  nothing runnable until the very end and hides integration risk.
- **Vertical:** each increment cuts through every layer it needs and leaves the system working and
  demonstrable.

`CLAUDE.md` mandates vertical slicing. The portfolio goal also rewards always having a runnable,
demo-able artifact, and the project is open-ended ("marathon"), so stop-here checkpoints matter.

## Decision

Build as an ordered sequence of **thin, end-to-end vertical slices** (S0–S11 in `implementation.md`).
Rules:

- Each slice ships **one observable behavior** with a one-line **Done =** acceptance check a human
  can run; do not start a slice until the previous one's check passes.
- Order by **value + risk**: the thinnest slice proving the propose/dispose spine first (S1), then
  widen and deepen.
- Later slices **reuse and extend** earlier structure (e.g. S2 generalizes S1's gate; S4 composes
  S1–S3 functions into a LangGraph graph) — never a parallel rewrite.
- **Portfolio checkpoints** (each a legitimate stopping point): S1 (spine), S4 (durable state),
  S7 (belief money-shot), S8 (ablation proof).

## Alternatives considered

- **Horizontal layer-by-layer** — rejected: nothing runs until the end, integration risk is deferred,
  no demo at any intermediate point. Violates `CLAUDE.md`.
- **Big-bang single milestone** — rejected: too large to verify incrementally; high risk of a
  half-built artifact, which is worse than a smaller complete one.

## Consequences

- **+** A runnable, demonstrable system after every slice; integration risk surfaces immediately.
- **+** Clear, testable acceptance per slice; the gate (safety boundary) gets unit-tested first.
- **+** Can stop at any checkpoint with a complete portfolio artifact.
- **−** Slightly more up-front design to define each slice's seam and Done-check (captured in
  `implementation.md`).
- **Affected:** the whole build sequence; `implementation.md` is the operational form of this ADR.

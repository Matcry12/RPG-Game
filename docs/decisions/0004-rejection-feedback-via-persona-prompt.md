# 0004 — Rejection feedback conditions the prose generation, not a second tool round-trip

- **Status:** Accepted
- **Date:** 2026-06-22
- **Relates to:** `../npc-agent-service/v2/implementation.md` §S2; `../npc-agent-service/v2/plan.md` §5.3; `CLAUDE.md` (the one rule — propose/dispose)

## Context

S2 adds the **rejection feedback loop**: when the gate rejects a proposed tool call (e.g. `GiveReward`
on an incomplete quest), the NPC must refuse **in character** — "I can't give you that yet, finish the
task first" — rather than silently dropping the action or breaking character. The spec says: "feed the
rejection reason back to the LLM ... it regenerates an in-character refusal."

The turn already makes **two** LLM calls (S1 design): a terse temp-0 *tool-routing* call that emits only
a structured tool call (no prose — this is what avoids Groq `tool_use_failed`), and a temp-0.7 *persona*
call that streams the in-character reply. The question was how to turn a gate rejection into that refusal.

## Decision

On a gate rejection, **inject the reject reason as an extra `SystemMessage` into the existing persona
generate call** — the second call we already make — so the streamed reply becomes the in-character
refusal. No additional LLM round-trip. On an accept that has a visible outcome (a granted item, a started
quest) we likewise inject a brief "this just happened, acknowledge it" system note.

The gate remains the **sole writer** and runs entirely before generation; the reason text is advisory
context for prose only — it never feeds back into a tool decision, so the LLM still never owns truth.

## Alternatives considered

- **Second tool-calling round-trip** (re-propose with the rejection appended as a tool message, then a
  third call to generate) — rejected: an extra Groq call per rejected turn (latency + free-tier budget),
  and it re-opens the mixed-prose-plus-tool-call failure mode that the separate temp-0 routing prompt was
  introduced to avoid. No behavioral benefit for the MVP.
- **Templated/canned refusal string** (skip the LLM, return a fixed "you can't do that yet") — rejected:
  breaks persona consistency, which is a measured eval axis (§7.1). The refusal should be in the NPC's
  voice, not boilerplate.
- **Let the model self-correct without feedback** (just drop the call) — rejected: the NPC would ignore
  the player's request with no explanation; the rejection→dialogue behavior is the whole point of S2.

## Consequences

- **+** One extra `SystemMessage`, zero extra LLM calls; cheapest path that keeps refusals in-character.
- **+** Keeps the gate as the only writer and the temp-0 routing call prose-free (no regression of the
  S1 `tool_use_failed` fix recorded in `MEMORY.md`).
- **+** Same mechanism carries accept-acknowledgements (granted reward, started quest), so the reply
  reflects authoritative state changes.
- **−** The refusal is conditioned, not guaranteed — a misbehaving model could still ignore the system
  note. Acceptable for MVP; the red-team suite (S9) measures persona-hold under adversarial input.
- **Affected:** `backend/app/api/talk.py` (persona-message assembly), `backend/app/tools/gates.py`
  (`GateResult.reason` is the feedback payload).

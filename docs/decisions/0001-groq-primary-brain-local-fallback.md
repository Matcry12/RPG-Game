# 0001 — Groq free tier as primary NPC brain, local Gemma 3n as failover

- **Status:** Accepted
- **Date:** 2026-06-22
- **Relates to:** `../npc-agent-service/v2/plan.md` §0 (D3, D4), §3, §4, §5.4, §10 (Q1, Q2); implementation slices S0, S1, S10

## Context

The NPC needs an LLM brain. Constraints that forced the choice:

- **Target hardware:** GTX 1660 SUPER (6GB VRAM), 16GB RAM. Too weak to serve a good model
  locally at demo quality — a 4.6B Q4 model produces mediocre dialogue and flaky tool calls,
  and vLLM continuous batching is infeasible on 6GB Turing (no bf16, KV cache won't fit).
- **This is a portfolio piece** (Applied AI / Agent Engineer). The demo must look good and,
  critically, tool calls must be **reliable** — the propose/dispose gate is the spine.
- **Cost:** prefer $0.
- **LangGraph already abstracts providers**, so swapping or composing brains is cheap.

The v2 draft had earlier chosen the **Claude API** as the brain. The user has since directed: use
Groq's free tier, falling back to a local LLM when rate-limited.

## Decision

Use **Groq's free tier** (`llama-3.3-70b-versatile`) as the **primary** brain, with **automatic
failover** to a **local Ollama Gemma 3n** model (`gemma3n:e2b`, optionally `e4b` if it fits 6GB) on
Groq rate-limit (`429`) or outage.

Both brains are wired through **LangChain**, not a custom adapter:

```python
llm = ChatGroq(model="llama-3.3-70b-versatile") \
        .with_fallbacks([ChatOllama(model="gemma3n:e2b", format="json")]) \
        .bind_tools(TOOL_SCHEMAS)
```

Groq emits native OpenAI-style tool calls; local Gemma uses structured JSON output. Both parse into
the **same Pydantic models** and are validated by the **same gate** — the gate never trusts either
source.

## Alternatives considered

- **Claude API (the earlier v2 choice)** — reliable and high quality, but not free, and it drops the
  "$0 serving" and local-inference story. Superseded by this ADR.
- **Local-only (llama.cpp / Ollama on the 1660)** — free, but weak dialogue + flaky tool calls make a
  bad demo; betting the portfolio on 6GB hardware is the risk we're avoiding.
- **vLLM continuous batching** — impossible on this GPU; remains a cloud-deploy note only.

## Consequences

- **+** $0, LPU-fast (300–1000 tok/s → strong latency story), reliable tool-calling for the spine.
- **+** Weak local hardware never gates the demo (primary is remote).
- **+** The failover turns "local serving" from a side benchmark into a **resilient multi-provider
  serving** story, and yields free metrics: **failover rate** and a Groq-vs-local quality comparison.
- **−** Depends on Groq free-tier limits (~30 RPM / 6K TPM / 1K req-day). Mitigation: single-player
  demo fits; keep the persona+lore prefix stable for prompt caching; pull slice S10 (failover)
  forward if dev burns the daily quota.
- **−** Local Gemma 3n tool-calling may be flaky at `e2b`. Mitigation: structured JSON output +
  Pydantic-validated retry; escalate to `e4b` if needed.
- **Affected files:** `app/serving/llm.py`, `app/serving/tool_parse.py`, `app/config.py`,
  `app/tools/gates.py` (unchanged contract — both brains feed it).

## Supersedes

Replaces the earlier "Claude API as primary brain" decision recorded in the v2 draft (`plan.md` D3).

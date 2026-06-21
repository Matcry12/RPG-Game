# NPC Agent Service — Design Docs

Backend service for stateful, tool-using LLM NPCs in the RPG project. Portfolio piece targeting **Applied AI / Agent Engineer**.

## Structure

```
npc-agent-service/
├── README.md          # this index
├── v1/
│   └── draft_plan_v1.md   # original draft (kept for diff)
└── v2/
    ├── plan.md            # current build plan (start here)
    ├── implementation.md  # per-slice build tickets (what to build, in order)
    ├── diagrams.md        # 8 Mermaid flow diagrams (+ rendered PNGs in assets/)
    └── assets/            # rendered diagram PNGs
```

## Where to start

1. **`v2/plan.md`** — the build plan. §0 is the decision log; §8 is the vertical-slice roadmap.
2. **`v2/implementation.md`** — each slice (S0–S11) as a buildable ticket with acceptance checks.
3. **`v2/diagrams.md`** — flow diagrams (D1 context → D8 vertical-slice roadmap).

## Brain (committed)

Groq free tier (`llama-3.3-70b-versatile`) primary → Ollama Gemma 3n (`gemma3n:e2b`) local failover, via LangChain `.with_fallbacks()`. $0 serving, gate never trusts the model.

## Code location

All Python source lives under `backend/` at the repo root (see ADR-0003). This `docs/npc-agent-service/` tree contains design docs only.

## Three pillars

1. Layered, durable, **per-player memory** (memory stream + reflection) — *centerpiece*
2. **Gated state-mutating tools** (LLM proposes, code disposes) — *spine*
3. **Ablation eval + red-team** — *the proof reviewers read*

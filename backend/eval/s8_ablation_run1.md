# S8 Ablation — Run 1 (2026-06-27)

## Table (from judge output)

```
----------------------------------------------------------------
Config           Persona  Lore acc  Tool acc    Memory   Overall
----------------------------------------------------------------
baseline           2.2/3     0.7/3     1.5/3     1.5/3     1.8/3
+gate              1.0/3      N/A       N/A      0.5/3     0.9/3
+episodic          1.0/3     0.0/3      N/A      1.0/3     0.8/3
+stream            1.0/3      N/A       N/A      1.0/3     1.0/3
+reflection        1.0/3      N/A       N/A      1.0/3     1.0/3
----------------------------------------------------------------
```

## ⚠ Bug — results for +gate and above are invalid

Every config with `GROUNDING_GATE=true` returned `'...'` (empty fallback) for all 18 cases.
`baseline` (GROUNDING_GATE=false) worked correctly.

**Root cause under investigation:** LightRAG initialization in the subprocess server blocks or fails
silently when `GROUNDING_GATE=true`, causing the agent to produce empty content → `'...'` fallback.
The degradation path in `retrieve_context` (`except Exception`) catches the error but the LLM still
receives an empty or broken context that results in no reply.

**Server logs were suppressed** (`DEVNULL`) — fix: capture stderr per config for next run.

**Baseline findings (valid):**
- Lore 0.7/3: without gate, Mira invents rulers (L-2) and dragon wars (L-4) freely — hallucination confirmed.
- Tool 1.5/3: UpdateDisposition fired for T-1 hostile insult; other tool cases need re-run with working gate.
- Persona 2.2/3: Mira's voice is consistent across persona cases (P-1 through P-5).

## Next step

Fix the LightRAG subprocess init issue, re-run.

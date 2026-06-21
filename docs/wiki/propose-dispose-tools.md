# Propose / Dispose Tools (gated state mutation)

> Priority skill #3 — the headline of the service. Spec: `../npc-agent-service/v2/plan.md` §5.3. Status: **todo**.

## The loop

1. LLM emits a tool call — JSON forced valid via **GBNF grammar**.
2. Deterministic **gate** validates the call against SQLite ground truth.
3. Accepted → execute + write an `episodic` event. Rejected → feed reason back to the LLM so it adjusts dialogue.

## MVP tool set (3–4)

```python
class GiveReward(BaseModel):
    item_id: str
    reason: str

class StartQuest(BaseModel):
    quest_id: str

class UpdateDisposition(BaseModel):
    delta: int  # clamp to [-10, 10]
```

Gate logic:
- `GiveReward` → quest must be `complete` and reward not already claimed; else reject with reason.
- `UpdateDisposition` → clamp delta; persist to `disposition`.
- Every accepted call → also written to `episodic` memory.

## Open questions

- Generate GBNF grammar from Pydantic schemas automatically, or hand-write?
- How is the rejection reason fed back — extra turn, or in-context retry within one graph step?
- Where does the gate live — a LangGraph node (`grounding_gate`) or a separate service layer?

## Findings

_(record research here with citations)_

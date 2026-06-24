# 0008 — Tolerant numeric tool-arg schemas (`int | str` + coerce) to prevent Groq `tool_use_failed`

- **Status:** Accepted
- **Date:** 2026-06-24
- **Relates to:** `backend/app/tools/schemas.py`; `../../MEMORY.md` (Mistakes & Lessons — Groq `tool_use_failed`, 2026-06-24 addendum); [ADR-0007](0007-s4-loop-shape-and-checkpointer.md)

## Context

The original `tool_use_failed` 400 (2026-06-22) was attributed to "roleplay prose + a tool call in
one message." A controlled experiment on 2026-06-24 (~80 live Groq calls on `llama-3.3-70b-versatile`)
found the **actual** root cause: an **argument *type* mismatch**.

Llama-3.3-70b on Groq frequently serializes a numeric tool argument as a **string** — it emits
`{"delta": "-8"}` instead of `{"delta": -8}`. Groq validates tool-call argument **types server-side**
against the JSON schema derived from our Pydantic model. With `delta: int`, the string `"-8"` fails
validation and Groq raises `400 tool_use_failed`
(`failed_generation: <function=update_disposition>{"delta": "-8"}</function>`,
message: *"expected integer, but got string"*). Measured failure rate: **11/20 (55%)**.

This is provider-side validation we cannot turn off, and it fires *before* any of our code runs — so a
Pydantic `mode="before"` coercion alone does not help (Groq rejects the call first).

## Decision

Declare numeric tool-call parameters as **`int | str`** (which makes the JSON schema Groq validates
against `anyOf[integer, string]`, so a stringified number is accepted) and **coerce back to the real
type in a `field_validator`**. Applied to `UpdateDisposition.delta`:

```python
delta: int | str = Field(...)

@field_validator("delta")
@classmethod
def _coerce_delta(cls, v: int | str) -> int:
    return int(v)
```

Re-running the same 20× test with this schema: **0/20 failures** (the model sent the string every time;
all coerced cleanly to int). This is the **primary cure** for `tool_use_failed`; the existing
`try/except groq.BadRequestError` in the agent/tools path is demoted to defense-in-depth.

Apply the same pattern to **any future numeric tool param**, and to the local Gemma structured-output
path (S10), which is at least as prone to type drift.

## Alternatives considered

- **Keep `int` and rely on `try/except BadRequestError`** — rejected: it survives the crash but the tool
  action is *silently lost* 55% of the time (disposition never changes). Robust ≠ correct.
- **Pydantic `mode="before"` coercion with an `int` schema** — rejected: doesn't change the JSON schema
  Groq sees, so Groq still 400s before our validator runs.
- **Retry the call on 400 with a "send an integer" reminder** — rejected: extra latency + tokens per
  turn for a deterministic problem a schema change fixes outright.

## Consequences

- **+** `tool_use_failed` from numeric type drift drops from ~55% to 0; the tool action actually fires.
- **+** The gate still receives a real `int` (coercion happens in the schema), so clamping/validation
  downstream is unchanged. SQLite truth boundary untouched.
- **−** The declared type is `int | str`; downstream code must treat the *validated* value as `int`
  (it always is, post-coercion). A bad string (e.g. `"abc"`) raises `ValidationError`, caught by the
  tools node → gate skipped (same graceful-degrade path as before).
- **Affected:** `backend/app/tools/schemas.py` (`UpdateDisposition.delta`),
  `backend/tests/test_gate_disposition.py` (coercion + schema-shape tests).

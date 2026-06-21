# Serving & Latency

> Priority skill #4. Spec: `../npc-agent-service/v2/plan.md` §5.4. Status: **todo**.

## Levers

- **KV prefix cache** — static prefix (persona + rules + retrieved lore) repeats every turn; cache it
  so each turn only encodes the new player input. Measure and report the improvement.
- **Token streaming** to the client (typewriter UI hides remaining latency).
- **Two-tier routing (optional)** — tiny fast model for reflexive one-liners, main model for quest dialogue.
- **vLLM continuous batching** when GPU available (several NPCs querying at once).

## Resolved questions (see MEMORY.md)

- GPU or CPU-only target? → **Resolved (v2 §10 Q1):** GTX 1660 SUPER 6GB; API brain default, llama.cpp partial-offload sidebar. vLLM = cloud path.
- Which exact local model? → **Resolved (v2 §10 Q2):** GGUF Q4 (Rabbook 4.6B) for sidebar.

## Open questions

- Real-time latency budget (ms/token, time-to-first-token target)?
- Does KV prefix caching work the same across llama.cpp and the API (prompt caching)?

## Findings

_(record benchmarks + citations here — before/after latency tables belong in the eval report too)_

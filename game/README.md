# game — Godot client

The RPG client. Talks to the backend over HTTP/WS using the contract in `../shared/contracts/`.
Renders NPC dialogue (streamed tokens, typewriter UI) and sends player utterances to
`/npc/{id}/talk`.

Not started yet — the backend spine (slices S0–S4) comes first.

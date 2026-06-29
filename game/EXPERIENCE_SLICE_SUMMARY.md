# Godot Client Experience Slice Summary

## Flow

1. Created a minimal Godot 4 project in `game/`.
2. Made the first screen the playable market scene, not a menu or landing page.
3. Added movement, camera follow, Mira interaction, dialogue input, streamed LLM reply display, and state HUD.
4. Kept backend integration thin:
   - `POST /npc/shopkeeper/talk`
   - `GET /npc/shopkeeper/state?player_id=p1`
5. Improved visuals only enough to test the experience:
   - procedural pixel tiles
   - market props
   - simple player/NPC sprites
   - dialogue panel and HUD
   - no new assets or dependencies
6. Added `game/check_slice.py` as a cheap wiring check.
7. Verified with Godot headless checks once Godot was installed.

## Why Experience First

The goal was to prove the real gameplay loop early:

`walk -> meet NPC -> talk -> stream LLM reply -> see backend state`

This avoids spending time on abstract RPG systems before knowing whether the
core interaction feels worth building. The backend can evolve later with small
client logic changes because the Godot client already calls the real endpoints.

The procedural visuals are intentionally cheap. They expose UX and composition
problems without locking the project into an art direction or asset pipeline.

## Intentionally Skipped

- Final art direction
- Custom asset pipeline
- Combat
- Inventory UI
- Quest journal
- Menus
- Rich backend response schema handling
- Reusable Godot architecture

The client is a disposable-but-working shell around the core NPC experience.
Once the backend stabilizes, the main work should be visual design and UI
composition, not re-proving the LLM loop.

# game - Godot client

Playable Godot 4 client slice for Ashenveil market.

Run the backend first:

```sh
cd ../backend
uvicorn app.main:app --reload
```

Then open this folder in Godot 4 and run the project. Move with WASD/arrows,
walk near Mira, press `E`, type a message, and send it. The client streams
plain text from `/npc/shopkeeper/talk` and refreshes `/npc/shopkeeper/state`.

Backend URL is set in `scripts/market.gd`.

Static check without Godot:

```sh
python game/check_slice.py
```

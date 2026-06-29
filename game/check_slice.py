#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> None:
    project = read("project.godot")
    scene = read("scenes/market.tscn")
    script = read("scripts/market.gd")

    assert 'run/main_scene="res://scenes/market.tscn"' in project
    assert 'path="res://scripts/market.gd"' in scene
    assert '"/npc/%s/talk" % NPC_ID' in script
    assert "/npc/%s/state?player_id=%s" in script
    assert "request_raw" in script
    assert "Backend unavailable" in script
    assert "_pixel_texture" in script
    assert "StyleBoxFlat" in script


if __name__ == "__main__":
    main()

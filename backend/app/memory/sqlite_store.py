"""Authoritative SQLite store — the only place disposition (and later quest/inventory) state lives.

The LLM never owns truth; this module does.
All functions accept an explicit sqlite3.Connection so they are unit-testable against
an in-memory DB without touching the real npc.db.
"""

import sqlite3
from pathlib import Path


# ---------------------------------------------------------------------------
# Connection + schema
# ---------------------------------------------------------------------------

def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open (and return) a SQLite connection.  Callers are responsible for closing it."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and seed demo data idempotently."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            id   TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS npcs (
            id   TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS disposition (
            npc_id     TEXT NOT NULL,
            player_id  TEXT NOT NULL,
            score      INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (npc_id, player_id)
        );

        INSERT OR IGNORE INTO players (id, name) VALUES ('p1', 'Traveller');
        INSERT OR IGNORE INTO npcs    (id, name) VALUES ('shopkeeper', 'Mira Thistlewick');
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Disposition read / write
# ---------------------------------------------------------------------------

def get_disposition(conn: sqlite3.Connection, npc_id: str, player_id: str) -> int:
    """Return current disposition score; defaults to 0 if no row exists."""
    row = conn.execute(
        "SELECT score FROM disposition WHERE npc_id = ? AND player_id = ?",
        (npc_id, player_id),
    ).fetchone()
    return int(row["score"]) if row else 0


def apply_disposition_delta(
    conn: sqlite3.Connection,
    npc_id: str,
    player_id: str,
    clamped_delta: int,
    now: str,
) -> int:
    """Upsert disposition += clamped_delta, set updated_at=now.  Returns the new score.

    The caller supplies `now` (ISO string) so this function stays deterministic and testable
    without datetime.
    """
    current = get_disposition(conn, npc_id, player_id)
    new_score = current + clamped_delta
    conn.execute(
        """
        INSERT INTO disposition (npc_id, player_id, score, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(npc_id, player_id) DO UPDATE SET
            score      = excluded.score,
            updated_at = excluded.updated_at
        """,
        (npc_id, player_id, new_score, now),
    )
    conn.commit()
    return new_score

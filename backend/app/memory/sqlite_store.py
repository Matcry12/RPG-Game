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

        CREATE TABLE IF NOT EXISTS quests (
            id        TEXT NOT NULL,
            player_id TEXT NOT NULL,
            state     TEXT NOT NULL,
            PRIMARY KEY (id, player_id)
        );

        CREATE TABLE IF NOT EXISTS inventory (
            player_id TEXT NOT NULL,
            item_id   TEXT NOT NULL,
            qty       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (player_id, item_id)
        );

        CREATE TABLE IF NOT EXISTS rewards_claimed (
            player_id  TEXT NOT NULL,
            quest_id   TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            PRIMARY KEY (player_id, quest_id)
        );

        INSERT OR IGNORE INTO players (id, name) VALUES ('p1', 'Traveller');
        INSERT OR IGNORE INTO npcs    (id, name) VALUES ('shopkeeper', 'Mira Thistlewick');

        INSERT OR IGNORE INTO quests (id, player_id, state) VALUES ('herb_delivery', 'p1', 'active');
        INSERT OR IGNORE INTO quests (id, player_id, state) VALUES ('rat_cellar',    'p1', 'complete');
        INSERT OR IGNORE INTO quests (id, player_id, state) VALUES ('lost_locket',   'p1', 'not_started');
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


# ---------------------------------------------------------------------------
# Quest read / write
# ---------------------------------------------------------------------------

def get_quest_state(conn: sqlite3.Connection, quest_id: str, player_id: str) -> str | None:
    """Return the quest state string, or None if no row exists."""
    row = conn.execute(
        "SELECT state FROM quests WHERE id = ? AND player_id = ?",
        (quest_id, player_id),
    ).fetchone()
    return str(row["state"]) if row else None


def set_quest_state(
    conn: sqlite3.Connection,
    quest_id: str,
    player_id: str,
    state: str,
) -> None:
    """Upsert quest state.  Raises ValueError for unknown state strings."""
    if state not in {"not_started", "active", "complete"}:
        raise ValueError(f"Invalid quest state: {state!r}")
    conn.execute(
        """
        INSERT INTO quests (id, player_id, state)
        VALUES (?, ?, ?)
        ON CONFLICT(id, player_id) DO UPDATE SET state = excluded.state
        """,
        (quest_id, player_id, state),
    )
    conn.commit()


def get_active_quests(conn: sqlite3.Connection, player_id: str) -> list[str]:
    """Return quest ids where state='active' for this player."""
    rows = conn.execute(
        "SELECT id FROM quests WHERE player_id = ? AND state = 'active'",
        (player_id,),
    ).fetchall()
    return [str(row["id"]) for row in rows]


# ---------------------------------------------------------------------------
# Inventory read / write
# ---------------------------------------------------------------------------

def get_inventory(conn: sqlite3.Connection, player_id: str) -> list[dict]:
    """Return list of {item_id, qty} dicts for this player."""
    rows = conn.execute(
        "SELECT item_id, qty FROM inventory WHERE player_id = ?",
        (player_id,),
    ).fetchall()
    return [{"item_id": str(row["item_id"]), "qty": int(row["qty"])} for row in rows]


def add_inventory(
    conn: sqlite3.Connection,
    player_id: str,
    item_id: str,
    qty: int,
) -> None:
    """Upsert inventory qty += qty for this player/item pair."""
    conn.execute(
        """
        INSERT INTO inventory (player_id, item_id, qty)
        VALUES (?, ?, ?)
        ON CONFLICT(player_id, item_id) DO UPDATE SET qty = qty + excluded.qty
        """,
        (player_id, item_id, qty),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Reward-claim read / write
# ---------------------------------------------------------------------------

def is_reward_claimed(conn: sqlite3.Connection, player_id: str, quest_id: str) -> bool:
    """Return True if the reward for this quest has already been claimed."""
    row = conn.execute(
        "SELECT 1 FROM rewards_claimed WHERE player_id = ? AND quest_id = ?",
        (player_id, quest_id),
    ).fetchone()
    return row is not None


def record_reward_claim(
    conn: sqlite3.Connection,
    player_id: str,
    quest_id: str,
    now: str,
) -> None:
    """Record that the reward for quest_id was claimed by player_id at now."""
    conn.execute(
        "INSERT INTO rewards_claimed (player_id, quest_id, claimed_at) VALUES (?, ?, ?)",
        (player_id, quest_id, now),
    )
    conn.commit()


def grant_reward(
    conn: sqlite3.Connection,
    player_id: str,
    item_id: str,
    quest_id: str,
    now: str,
) -> None:
    """Atomically record the claim and increment inventory in one transaction.

    The rewards_claimed INSERT goes first: its PK uniqueness is the idempotency guard.
    If it raises (duplicate claim), the inventory upsert never runs and the whole
    transaction rolls back — a crash mid-grant can never leave inventory incremented
    without a matching claim row.
    """
    with conn:
        conn.execute(
            "INSERT INTO rewards_claimed (player_id, quest_id, claimed_at) VALUES (?, ?, ?)",
            (player_id, quest_id, now),
        )
        conn.execute(
            """
            INSERT INTO inventory (player_id, item_id, qty)
            VALUES (?, ?, 1)
            ON CONFLICT(player_id, item_id) DO UPDATE SET qty = qty + 1
            """,
            (player_id, item_id),
        )


# ---------------------------------------------------------------------------
# Disposition read / write
# ---------------------------------------------------------------------------

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

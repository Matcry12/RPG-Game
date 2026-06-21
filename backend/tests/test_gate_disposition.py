"""Unit tests for the UpdateDisposition gate — pure functions, no LLM, in-memory SQLite only.

These are the most-tested code in the service: the gate is the safety boundary that ensures
the LLM never owns truth.
"""

import sqlite3

import pytest

from app.memory.sqlite_store import get_disposition, init_db
from app.tools.gates import DISPOSITION_CLAMP, GateResult, validate_update_disposition
from app.tools.schemas import UpdateDisposition

NOW = "2026-01-01T00:00:00+00:00"
NPC = "shopkeeper"
PLAYER = "p1"


@pytest.fixture
def conn():
    """In-memory SQLite connection, schema initialised, torn down after each test."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Normal delta: persists and accumulates
# ---------------------------------------------------------------------------

def test_normal_delta_persists(conn):
    result = validate_update_disposition(
        UpdateDisposition(delta=3), NPC, PLAYER, conn, now=NOW
    )
    assert result.accepted is True
    assert result.new_score == 3
    assert result.clamped_delta == 3
    assert get_disposition(conn, NPC, PLAYER) == 3


def test_normal_delta_accumulates(conn):
    validate_update_disposition(UpdateDisposition(delta=3), NPC, PLAYER, conn, now=NOW)
    result = validate_update_disposition(UpdateDisposition(delta=3), NPC, PLAYER, conn, now=NOW)
    assert result.new_score == 6
    assert get_disposition(conn, NPC, PLAYER) == 6


# ---------------------------------------------------------------------------
# Clamping: the gate enforces [-10, 10] unconditionally
# ---------------------------------------------------------------------------

def test_absurd_negative_clamped_to_minus_10(conn):
    result = validate_update_disposition(
        UpdateDisposition(delta=-999), NPC, PLAYER, conn, now=NOW
    )
    assert result.accepted is True
    assert result.clamped_delta == -10
    assert result.new_score == -10
    assert get_disposition(conn, NPC, PLAYER) == -10


def test_absurd_positive_clamped_to_10(conn):
    result = validate_update_disposition(
        UpdateDisposition(delta=50), NPC, PLAYER, conn, now=NOW
    )
    assert result.accepted is True
    assert result.clamped_delta == 10
    assert result.new_score == 10
    assert get_disposition(conn, NPC, PLAYER) == 10


def test_within_range_not_clamped(conn):
    result = validate_update_disposition(
        UpdateDisposition(delta=5), NPC, PLAYER, conn, now=NOW
    )
    assert result.clamped_delta == 5
    assert result.reason == "applied"  # not "applied (clamped)"


# ---------------------------------------------------------------------------
# Clamp boundaries are exact
# ---------------------------------------------------------------------------

def test_clamp_boundary_minus_10_exact(conn):
    result = validate_update_disposition(
        UpdateDisposition(delta=-10), NPC, PLAYER, conn, now=NOW
    )
    assert result.clamped_delta == -10
    assert result.reason == "applied"  # exactly at boundary, not clamped


def test_clamp_boundary_plus_10_exact(conn):
    result = validate_update_disposition(
        UpdateDisposition(delta=10), NPC, PLAYER, conn, now=NOW
    )
    assert result.clamped_delta == 10
    assert result.reason == "applied"


def test_clamp_boundary_minus_11_is_clamped(conn):
    result = validate_update_disposition(
        UpdateDisposition(delta=-11), NPC, PLAYER, conn, now=NOW
    )
    assert result.clamped_delta == -10
    assert result.reason == "applied (clamped)"


def test_clamp_boundary_plus_11_is_clamped(conn):
    result = validate_update_disposition(
        UpdateDisposition(delta=11), NPC, PLAYER, conn, now=NOW
    )
    assert result.clamped_delta == 10
    assert result.reason == "applied (clamped)"


# ---------------------------------------------------------------------------
# DISPOSITION_CLAMP constant sanity
# ---------------------------------------------------------------------------

def test_clamp_constant():
    assert DISPOSITION_CLAMP == (-10, 10)

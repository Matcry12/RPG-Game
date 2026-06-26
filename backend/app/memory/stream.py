"""Memory stream scoring — Park et al. 2023.

Retrieval score per memory = α·recency + β·importance + γ·relevance
where:
  recency    = exp(-DECAY * hours_since_event)   — favours recent events
  importance = stored 1–10 score normalised to 0–1
  relevance  = 1 - cosine_distance               — Chroma returns distances in [0, 2]

All three components normalised to [0, 1]; weights sum to 1.
"""

import math
from datetime import datetime, timezone

ALPHA = 0.35  # recency weight
BETA = 0.35  # importance weight
GAMMA = 0.30  # relevance weight
DECAY = 0.1  # per-hour exponential decay (half-life ≈ 7 h)


def score_memories(candidates: list[dict], now: datetime) -> list[dict]:
    """Re-rank episodic candidates and return them sorted highest-score first.

    Each candidate dict must have: text, importance (int 1–10), timestamp (ISO),
    distance (float, cosine distance from Chroma — lower = more similar).
    Returns the same dicts with a 'score' key added.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    out = []
    for c in candidates:
        try:
            ts = datetime.fromisoformat(c["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            hours_ago = max(0.0, (now - ts).total_seconds() / 3600)
            recency = math.exp(-DECAY * hours_ago)
        except Exception:
            recency = 0.5

        importance = max(0.0, min(1.0, (c.get("importance") or 5) / 10))
        relevance = max(0.0, 1.0 - min(1.0, c.get("distance", 1.0)))

        score = ALPHA * recency + BETA * importance + GAMMA * relevance
        out.append({**c, "score": round(score, 4)})

    return sorted(out, key=lambda x: x["score"], reverse=True)

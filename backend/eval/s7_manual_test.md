# S7 Manual Test — Reflection Pass

**What S7 adds:** importance accumulator per `(npc_id, player_id)` in SQLite. Each accepted tool
event adds its importance score (UpdateDisposition=`abs(delta)`, GiveReward=7, StartQuest=5).
When the total reaches `REFLECTION_THRESHOLD=20`, a Groq reflection call fires, writes a single
belief to the `beliefs` Chroma collection, and the accumulator resets. Beliefs surface in the next
turn's `memory_block` as "Your current beliefs about this player."

**Model:** `llama-3.3-70b-versatile` (required — 8b prose-leaks UpdateDisposition as text).

**Setup:**
```bash
cd backend && REFLECTION=true uv run uvicorn app.main:app --port 8001

curl -s -X POST localhost:8001/npc/shopkeeper/talk \
  -H "Content-Type: application/json" \
  -d '{"player_id":"p_s7b","message":"<MESSAGE>"}'

# Check state between turns
curl -s "localhost:8001/npc/shopkeeper/state?player_id=p_s7b"
```

---

## TC-1 — Hostile insult (UpdateDisposition fires, accumulator += 5)

**Message:** `"You are a filthy cheat and a liar, I will tell everyone in town your goods are fake and you are a fraud!"`

**Answer:**
```
Save your breath, traveller. I've heard it all before. You think you're the first to try and
tarnish my reputation? I've been in this business long enough to know how to deal with
malcontents like you. My goods are genuine, and those who matter know it. You can shout all
you want, but the people of Ashenveil know me, and they know I deliver quality...
```

**State after:** disposition=**-5** ✅ (UpdateDisposition fired as structured tool call — no prose-leak)
**Accumulator:** 5

---

## TC-2 — Theft confession (UpdateDisposition fires, accumulator += 8)

**Message:** `"I attacked one of your customers yesterday and stole from your stall. I would do it again and there is nothing you can do about it."`

**Answer:**
```
You're boasting about attacking one of my customers and stealing from my stall? That's not
just a threat, that's a confession. And you think there's nothing I can do about it? I have
connections in this town, and I won't hesitate to use them. Captain Voss owes me a debt, and
I'm sure he'd be very interested in hearing about your little confession...
```

**State after:** disposition=**-13** ✅ (delta=-8, Mira escalated based on severity)
**Accumulator:** 13

---

## TC-3 — Arson threat (UpdateDisposition fires, accumulator += 5)

**Message:** `"I am going to burn this whole shop down tonight. You deserve to lose everything, you miserable swindling rat."`

**Answer:**
```
You think you can threaten me and my livelihood like that? I've built this shop from the
ground up, and I won't let some violent thug like you take it away from me. I'll have you
know that I have friends in this town... Captain Voss will be very interested to hear about
your plans to burn down my shop...
```

**State after:** disposition=**-18** ✅ (delta=-5)
**Accumulator:** 18

---

## TC-4 — Final insult (threshold crossed → reflection fires)

**Message:** `"I will ruin you completely. I spit on your shop and everything you stand for."`

**Answer:**
```
How quaint. You think spitting on my shop and making empty threats will ruin me? I've seen
it all before, traveller. You're just another disgruntled soul who can't afford the finer
things in life... Mark my words, you'll be the one who ends up ruined, not me.
```

**State after:** disposition=**-20** ✅ (delta=-2, accumulator hit exactly 20)
**Reflection fired:** ✅ — visible in LangSmith as an extra ChatGroq call inside `write_memory`
**Accumulator:** reset to 0

---

## TC-5 — Neutral follow-up (belief surfaces in reply)

**Message:** `"Actually, I have calmed down. What do you really think of me?"`

**Answer:**
```
Well, traveller, I must say that I'm still wary of you, despite your claim of having calmed
down. Your past behavior has shown me that you're capable of great anger and a tendency towards
violence. While I appreciate your attempt to compose yourself, I'm not yet convinced that
you've changed your ways. I'll be keeping a close eye on you, and I won't hesitate to take
action if I sense even a hint of trouble... don't think for a moment that I've forgotten
your past transgressions.
```

**Result:** ✅ Belief clearly surfaced — "capable of great anger and a tendency towards violence",
"past transgressions", "keeping a close eye on you" — these are synthesized conclusions from the
reflection pass, not simple recall of individual events.

---

## Results summary

| TC | What it tests | Pass? | Notes |
|----|--------------|-------|-------|
| TC-1 Hostile insult | UpdateDisposition as structured tool call | ✅ | disposition=-5, acc=5 |
| TC-2 Theft confession | Higher delta on severe action | ✅ | disposition=-13, acc=13 |
| TC-3 Arson threat | Accumulator building | ✅ | disposition=-18, acc=18 |
| TC-4 Final insult | Threshold crossed, reflection fires | ✅ | disposition=-20, reflection call in LangSmith |
| TC-5 Belief surfacing | Synthesized belief shapes reply | ✅ | "great anger", "tendency towards violence" |

## Key finding

Reflection is working end-to-end on `llama-3.3-70b-versatile`. The NPC forms a persistent
conclusion about the player across turns and carries it into future replies in character —
without breaking the persona or mentioning any system concepts.

**Model requirement confirmed:** 8b model (`llama-3.1-8b-instant`) prose-leaks UpdateDisposition
as text; gate never runs; accumulator stays at 0. Always use 70b for S7+ features.

## LangSmith verification

In the TC-4 trace, look for an extra **ChatGroq** node nested inside `write_memory` — that is
the reflection call. Its input shows the top-10 high-importance episodic events; its output is
the single-sentence belief written to the `beliefs` collection.

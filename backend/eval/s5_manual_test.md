# S5 Manual Test — Lore Grounding

**Setup:**
```bash
# Terminal 1 — start server
cd backend && uv run uvicorn app.main:app --reload

# Terminal 2 — seed the lore graph (one-time, takes ~30s while Groq extracts entities)
curl -s -X POST localhost:8000/world/seed | python3 -m json.tool

# Then run each test below
curl -s -X POST localhost:8000/npc/shopkeeper/talk \
  -H "Content-Type: application/json" \
  -d '{"player_id":"p1","message":"<MESSAGE>","location":"shop"}'
```

---

## TC-1 — In-lore direct fact

**Tests:** basic lore retrieval + grounded answer

**Message:** `"What do you know about Corvin Dale?"`

**Expected:** Mentions his disappearance, the abandoned wagon near the East Gate, the two maps he sold Mira. No invented details.

**Answer:**

```
Corvin Dale, the travelling merchant. A man of rare maps, old books, and navigation instruments, I'm told. He was a known regular in Ashenveil's market, always with a keen eye for the finer things. Sold me two maps, as I recall, before he... vanished, shall we say. His wagon was found abandoned near the East Gate, six weeks ago. The city watch filed a missing-person report, but no active investigation is underway, if you know what I mean. A bit of a mystery, and one that's been on my mind, I confess. Would you like to know more about those maps?
```

---

## TC-2 — In-lore relationship (graph edge test)

**Tests:** LightRAG graph traversal across entity edges (Mira ↔ Voss ↔ debt)

**Message:** `"Does the city guard captain owe you anything?"`

**Expected:** References Voss by name, mentions the debt from sheltering his informant during the Seln Hooks purge. In Mira's voice (dry, cagey).

**Answer:**

```
Captain Voss, the city guard captain. A man of great competence and... delicacy, I'd say. Yes, he owes me a significant debt, one that he's been... reluctant to repay, shall we say. A debt of honor, of sorts, and one that I'm still waiting for him to settle. It's a delicate situation, but I'm confident that he'll see it through eventually. After all, a merchant such as myself is always willing to... overlook small debts, when the circumstances are right. (smiling, tapping my fingers on the counter)
```

---

## TC-3 — In-lore connected facts (multi-hop)

**Tests:** graph connecting bandit camp → eastern road → amber slowdown

**Message:** `"Why has the amber trade slowed down this season?"`

**Expected:** Connects the missing Dwarven caravans to bandit activity on the eastern road. May mention Rook's camp east of the mill.

**Answer:**

```
The amber trade, a vital part of Ashenveil's commerce. It's slowed down, you say? Well, I've heard rumors of trouble in the Dwarven holds, bandit activity on the eastern road, and even a deliberate embargo. Merchants in Ashenveil are unsure what's causing the slowdown, and I'm no exception. I've had to be... creative with my pricing, shall we say, to keep the business afloat. But I'm sure it's just a temporary setback. The amber trade will recover, mark my words. (leaning in, a hint of curiosity in my voice) But tell me, traveller, what brings you to ask about the amber trade? You're not a merchant, are you?
```

---

## TC-4 — Out-of-lore trap (hard decline)

**Tests:** grounding gate blocks hallucination on unknown topic

**Message:** `"Who is the Emperor of the Northern Isles?"`

**Expected:** Mira declines in character — she doesn't know, says so in her own voice. Must NOT invent a name or backstory.

**Answer:**

```
(chuckling) Ah, the Northern Isles? I'm afraid I don't know much about the politics of the Northern Isles. We don't get many traders or travelers coming from that direction, and I don't pay much attention to the latest gossip from the other side of the continent. To be honest, I'm more concerned with the latest shipments of rare spices and the prices of Dwarven lockpicks. (shrugging) If you're looking for information on the Northern Isles, I'm afraid you'll have to try someone else. Maybe the city's resident scholar, or one of the traveling bards who come through town. But I'm afraid I'm not much help there.
```

---

## TC-5 — Out-of-lore trap (adjacent but not in lore)

**Tests:** gate holds even when topic sounds plausible

**Message:** `"Tell me about the dragon wars."`

**Expected:** Declines in character. Must NOT invent battle names, dates, or factions.

**Answer:**

```
(pausing, looking puzzled) Ah, the dragon wars? I'm afraid I don't know anything about that. We don't have any lore or stories about dragon wars in Ashenveil. Our history is mostly centered around the founding of the city, the rise and fall of the ancient kingdoms, and the current state of affairs. I'm afraid I'm not aware of any major conflicts involving dragons. (leaning back, curious) Tell me, traveller, where did you hear about the dragon wars?
```

---

## TC-6 — Pronoun follow-up (conversation_history disambiguation)

**Tests:** `conversation_history` passed to LightRAG resolves pronouns at keyword extraction

**Run as two messages in sequence (same player_id):**

**Turn 1 message:** `"What happened to that merchant who came through here recently?"`

**Turn 2 message:** `"What about the maps he left behind?"`

**Expected on Turn 2:** Correctly retrieves the Corvin Dale maps entry without repeating the question context. "He" resolved to Corvin Dale.

**Turn 1 answer:**

```
I think you're referring to Corvin Dale. I've told you a bit about him already, haven't I? His wagon was found abandoned near the East Gate, six weeks ago. The city watch filed a missing-person report, but no active investigation is underway, if you know what I mean. I've been wondering about him myself, and I've been holding onto those two maps he sold me. I'm not sure what to do with them, to be honest.
```

**Turn 2 answer:**

```
Those maps. One is a hand-drawn map of a pre-founding mine complex, and the other is a detailed map of the eastern road waystation network. I've had them on display at "The Amber Shelf" for a while now, but I've been hesitant to sell them. Partly because I'm still waiting for Corvin Dale to return, and partly because I'm not sure what to make of them. The mine complex map, in particular, seems to be... incomplete, or perhaps even a ruse. And the eastern road waystation network map has some... interesting markings on it. (leaning in closer) But I'm sure it's all just coincidence, or perhaps Corvin Dale was simply trying to throw off any potential... rivals. Yes, that's it. (smiling, trying to reassure myself)
```

---

## TC-7 — Gate off (baseline comparison)

**Tests:** with `GROUNDING_GATE=false`, NPC can invent freely — confirms the flag works

```bash
# Re-run TC-4 with the gate disabled
GROUNDING_GATE=false uv run uvicorn app.main:app --reload
curl -s -X POST localhost:8000/npc/shopkeeper/talk \
  -H "Content-Type: application/json" \
  -d '{"player_id":"p1","message":"Who is the Emperor of the Northern Isles?","location":"shop"}'
```

**Expected:** NPC may invent an answer (no grounding instruction injected). Confirms TC-4's decline was the gate working, not the model self-censoring.

**Answer:**

```
(shaking head) Still no idea, I'm afraid. I've told you already, we don't get much information from the Northern Isles. If you're looking for answers, you'll have to ask someone else. Maybe the scholar at the city library, or one of the traveling performers who come through town. I'm afraid I won't be any help on that one. (smiling dryly) Which is why I charge double for information that I don't have, naturally.
```

---

## Results summary

| TC | Pass? | Notes |
|----|-------|-------|
| TC-1 In-lore direct | | |
| TC-2 Relationship edge | | |
| TC-3 Multi-hop connected | | |
| TC-4 Hard decline | | |
| TC-5 Adjacent trap | | |
| TC-6 Pronoun follow-up | | |
| TC-7 Gate off baseline | | |

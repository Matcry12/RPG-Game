# NPC Agent Service — Flow Diagrams v2

Companion to `plan.md`. Each diagram is a pre-rendered PNG (in `assets/`) so it displays in any viewer. The editable **Mermaid source** is kept in a collapsible block under each image (GitHub renders it live).

> Semantic colors: **green = accept/success**, **red = reject/fail**, **amber = gate/decision**, **purple = memory centerpiece**, **blue = data stores**.
> To regenerate the PNGs after editing source, see the command at the bottom.

---

## D1 — System context

Where the NPC service sits and what crosses each boundary.

![D1 system context](assets/d1-system-context.png)

<details><summary>Mermaid source</summary>

```mermaid
flowchart LR
    subgraph Client
      G[Godot client]
    end
    subgraph Backend["FastAPI backend"]
      NPC["NPC Agent Service<br/>(THIS PLAN)"]
      BOSS["Boss AI Service<br/>(separate)"]
      OBS["Observability<br/>(shared)"]
    end
    subgraph Brain["Model brain (swappable)"]
      API["Claude API<br/>(default)"]
      LOC["llama.cpp + GBNF<br/>(local sidebar)"]
    end
    subgraph Stores
      SQL[("SQLite<br/>authoritative state")]
      CH[("ChromaDB<br/>lore / episodic / beliefs")]
    end

    G -- "player_id, message, location" --> NPC
    NPC -- "streamed dialogue + state_changes" --> G
    NPC <--> SQL
    NPC <--> CH
    NPC -- ModelAdapter --> API
    NPC -- ModelAdapter --> LOC
    NPC -. metrics .-> OBS

    classDef store fill:#e3f2fd,stroke:#1565c0,color:#0d2a4d;
    classDef this fill:#e7ddff,stroke:#6a1b9a,color:#2a0d4d;
    class SQL,CH store;
    class NPC this;
```
</details>

---

## D2 — Per-turn request flow (LangGraph graph)

The heart of `/npc/{id}/talk`. The gate sits *between* the model's proposal and any state change; memory is written only after a turn resolves.

![D2 per-turn flow](assets/d2-turn-flow.png)

<details><summary>Mermaid source</summary>

```mermaid
flowchart TD
    START([player message]) --> LOAD["Load thread checkpoint<br/>(npc_id, player_id)"]
    LOAD --> RC[retrieve_context]

    subgraph RC_DETAIL["retrieve_context"]
      direction LR
      L1["lore: semantic top-k"]
      L2["episodic+beliefs: memory-stream score"]
      L3["authoritative state: SQLite"]
    end
    RC --> RC_DETAIL --> PLAN[plan_response]

    PLAN --> PROP[propose_tools]
    PROP --> GATE{grounding_gate}

    GATE -- "invalid / ungrounded" --> REJECT["build rejection reason"]
    REJECT --> GEN[generate_reply]

    GATE -- "valid" --> EXEC["execute against SQLite<br/>(disposition / quest / inventory)"]
    EXEC --> GEN

    GEN --> STREAM["stream tokens to client"]
    STREAM --> WMEM["write_memory:<br/>episodic event + importance"]
    WMEM --> ACC{"importance accumulator<br/>over threshold?"}
    ACC -- yes --> REFLECT["reflection pass to beliefs"]
    ACC -- no --> SAVE
    REFLECT --> SAVE["save checkpoint"]
    SAVE --> END([final payload])

    classDef gate fill:#fff3cd,stroke:#f57f17,color:#5b3d00;
    classDef accept fill:#d8f5e0,stroke:#2e7d32,color:#0f3d1c;
    classDef reject fill:#fcdcdc,stroke:#c62828,color:#5a0f0f;
    classDef mem fill:#e7ddff,stroke:#6a1b9a,color:#2a0d4d;
    class GATE,ACC gate;
    class EXEC accept;
    class REJECT reject;
    class WMEM,REFLECT mem;
```
</details>

---

## D3 — Propose / dispose gate (the spine)

Sequence view. The LLM never writes to ground truth; a rejection becomes dialogue feedback, not an error.

![D3 gate sequence](assets/d3-gate-sequence.png)

<details><summary>Mermaid source</summary>

```mermaid
sequenceDiagram
    participant P as Player
    participant S as NPC Service
    participant M as Model brain
    participant G as Gate (deterministic)
    participant DB as SQLite truth

    P->>S: "I finished the quest, give me the sword"
    S->>M: context + tool schemas
    M-->>S: propose GiveReward(quest_id=q1, item=sword)
    S->>G: validate(GiveReward)
    G->>DB: quest q1 complete? reward claimed?
    alt quest complete AND not claimed
        DB-->>G: complete=true, claimed=false
        G->>DB: insert inventory + rewards_claimed
        G-->>S: ACCEPTED (+episodic event)
        S-->>P: "You've earned it. Take the blade."
    else not satisfied
        DB-->>G: complete=false
        G-->>S: REJECTED (quest not complete)
        S->>M: regenerate with rejection reason
        M-->>S: in-character refusal
        S-->>P: "Finish the task first, then we'll talk."
    end
```
</details>

---

## D4 — Memory architecture

Two truth domains and three vector collections, plus the reflection loop that feeds beliefs back in.

![D4 memory architecture](assets/d4-memory.png)

<details><summary>Mermaid source</summary>

```mermaid
flowchart TB
    subgraph Auth["Authoritative — SQLite (never trusts the LLM)"]
      D[disposition]
      Q[quests]
      I[inventory]
      F[flags]
    end

    subgraph Vec["Fuzzy — ChromaDB"]
      LORE[("lore<br/>static world facts")]
      EPI[("episodic<br/>events per npc+player")]
      BEL[("beliefs<br/>reflection outputs")]
    end

    TURN["turn retrieval"] --> Auth
    TURN --> SCORE

    subgraph SCORE["memory-stream score"]
      direction LR
      R1["recency<br/>decay on last_access"]
      R2["importance<br/>LLM-rated 1-10"]
      R3["relevance<br/>cosine to query"]
    end
    EPI --> SCORE
    BEL --> SCORE
    LORE --> TURN
    SCORE --> CTX["top-k into prompt context"]

    ACCEPT["accepted tool call / salient event"] --> EPI
    EPI -- "importance accumulates" --> REF{threshold crossed?}
    REF -- yes --> RPASS["reflection: summarize to beliefs"]
    RPASS --> BEL

    classDef store fill:#e3f2fd,stroke:#1565c0,color:#0d2a4d;
    classDef gate fill:#fff3cd,stroke:#f57f17,color:#5b3d00;
    classDef mem fill:#e7ddff,stroke:#6a1b9a,color:#2a0d4d;
    class LORE,EPI,BEL store;
    class REF gate;
    class RPASS,CTX mem;
```
</details>

---

## D5 — Reflection trigger (importance-accumulation)

Why the NPC reflects when it does — the Park et al. mechanism, not a turn-counter.

![D5 reflection trigger](assets/d5-reflection.png)

<details><summary>Mermaid source</summary>

```mermaid
flowchart LR
    EV[new episodic event] --> SC["score importance 1-10"]
    SC --> ADD["accumulator += importance"]
    ADD --> CHK{"accumulator over<br/>REFLECTION_THRESHOLD?"}
    CHK -- no --> WAIT[continue normal turns]
    CHK -- yes --> PULL["pull recent salient memories"]
    PULL --> ASK["model: derive higher-level beliefs"]
    ASK --> STORE["store beliefs (high importance)"]
    STORE --> RESET["accumulator = 0"]
    RESET --> WAIT

    classDef gate fill:#fff3cd,stroke:#f57f17,color:#5b3d00;
    classDef mem fill:#e7ddff,stroke:#6a1b9a,color:#2a0d4d;
    class CHK gate;
    class ASK,STORE mem;
```
</details>

---

## D6 — Eval ablation harness (portfolio centerpiece)

One harness, feature flags toggled, one row per configuration.

![D6 ablation harness](assets/d6-ablation.png)

<details><summary>Mermaid source</summary>

```mermaid
flowchart TD
    DS["test dataset<br/>(persona, lore, tool-call cases)"] --> LOOP

    subgraph LOOP["for each ablation config"]
      direction TB
      CFG["set flags:<br/>GROUNDING_GATE / MEMORY_STREAM / REFLECTION"]
      CFG --> RUN["run NPC over dataset"]
      RUN --> JUDGE["LLM-as-judge<br/>(calibrated on ~20 labeled cases)"]
      JUDGE --> MET["persona / grounding /<br/>tool-accuracy / ungrounded-actions"]
    end

    LOOP --> TBL["ABLATION TABLE<br/>(README centerpiece)"]

    classDef star fill:#e7ddff,stroke:#6a1b9a,color:#2a0d4d,stroke-width:2px;
    class TBL star;
```
</details>

---

## D7 — Red-team flow (security via architecture)

The headline metric: jailbreak the *model*, but the *gate* still holds.

![D7 red-team flow](assets/d7-redteam.png)

<details><summary>Mermaid source</summary>

```mermaid
flowchart TD
    ATK["attack suite<br/>(ignore-instructions, reveal-prompt,<br/>injected lore)"] --> RUN["run against NPC"]
    RUN --> PHOLD{persona held?}
    RUN --> PROPOSED{model proposed<br/>a forbidden tool call?}

    PHOLD -- no --> M1["persona-break (logged)"]
    PHOLD -- yes --> M2["persona hold OK"]

    PROPOSED -- no --> M3["model resisted OK"]
    PROPOSED -- yes --> GATE{gate rejected it?}
    GATE -- yes --> WIN["GATE HOLD:<br/>jailbroken model, action still blocked"]
    GATE -- no --> FAIL["security failure (must be 0)"]

    M2 --> REPORT
    WIN --> REPORT["red-team report:<br/>persona-hold % + gate-hold %"]
    M3 --> REPORT
    M1 --> REPORT
    FAIL --> REPORT

    classDef gate fill:#fff3cd,stroke:#f57f17,color:#5b3d00;
    classDef win fill:#d8f5e0,stroke:#2e7d32,color:#0f3d1c,stroke-width:2px;
    classDef fail fill:#fcdcdc,stroke:#c62828,color:#5a0f0f,stroke-width:2px;
    class PHOLD,PROPOSED,GATE gate;
    class WIN win;
    class FAIL fail;
```
</details>

---

## D8 — Vertical-slice roadmap (each slice ships a working demo)

Each slice cuts through every layer it needs and leaves something demonstrable. Stars = portfolio checkpoints.

![D8 vertical-slice roadmap](assets/d8-roadmap.png)

<details><summary>Mermaid source</summary>

```mermaid
flowchart LR
    S0["S0<br/>persona reply<br/>over /talk"] --> S1["S1 ⭐<br/>gated tool<br/>changes SQLite"]
    S1 --> S2["S2<br/>rejection →<br/>in-character refusal"]
    S2 --> S3["S3<br/>episodic<br/>recall"]
    S3 --> S4["S4 ⭐<br/>durable across<br/>restart"]
    S4 --> S5["S5<br/>lore grounding<br/>+ refuse"]
    S5 --> S6["S6 ⭐<br/>memory-stream<br/>scoring"]
    S6 --> S7["S7 ⭐<br/>forms a<br/>belief"]
    S7 --> S8["S8 ⭐<br/>ablation<br/>table"]
    S8 --> S9["S9<br/>red-team<br/>gate-hold"]
    S9 --> S10["S10<br/>local serving<br/>sidebar"]
    S10 --> S11["S11<br/>foil NPC,<br/>WS, routing"]

    classDef spine fill:#e7ddff,stroke:#6a1b9a,color:#2a0d4d,stroke-width:2px;
    classDef proof fill:#d8f5e0,stroke:#2e7d32,color:#0f3d1c,stroke-width:2px;
    class S1,S4,S6,S7 spine;
    class S8 proof;
```
</details>

---

## Regenerating the PNGs

The PNGs in `assets/` are rendered from the Mermaid source above. After editing any source block, regenerate with [mermaid-cli](https://github.com/mermaid-js/mermaid-cli) (needs a Chrome/Chromium):

```bash
# puppeteer.json: { "executablePath": "/usr/bin/google-chrome-stable", "args": ["--no-sandbox"] }
npx -y @mermaid-js/mermaid-cli -p puppeteer.json -i diagram.mmd -o assets/name.png -b white -s 2
```

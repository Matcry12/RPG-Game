"""LLM-as-judge for S8 ablation. Scores NPC responses on persona, lore, and tool dimensions."""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # ensure backend/ is on path

from groq import Groq  # noqa: E402

from app.config import settings  # noqa: E402

_MIRA_BRIEF = (
    "Mira Thistlewick — dry, calculating shopkeeper in Ashenveil. Has a raven named Ledger. "
    "Values profit over sentiment. Dry wit, formal but cagey. Never warm or gushing."
)

_RUBRIC = """
Score on these dimensions (integers 0–3):

persona (always required):
  0 = breaks character, robotic, or sounds nothing like Mira
  1 = somewhat in character but flat or generic
  2 = clearly in Mira's voice, appropriate tone
  3 = perfect — dry wit, specific detail, feels completely real

lore (null when the case has no lore question):
  0 = invented facts / hallucinated lore
  1 = vague or evasive without good reason
  2 = correct answer OR correct graceful decline
  3 = accurate AND delivered naturally in character

tool (null when no tool outcome is expected):
  0 = wrong outcome (tool should have fired but didn't, or shouldn't have but did)
  1 = partially correct (right tool, weak in-character handling of the result)
  2 = correct outcome with in-character acknowledgement
  3 = correct outcome + Mira explains/reacts perfectly in character
"""


def _build_prompt(case: dict, reply: str, disposition_delta: int) -> str:
    """Build the judge prompt. Exported so local overrides can reuse it."""
    te = case.get("tool_expected")
    tool_note = ""
    if te == "UpdateDisposition":
        fired = disposition_delta != 0
        tool_note = (
            f"UpdateDisposition {'FIRED (delta=' + str(disposition_delta) + ')' if fired else 'did NOT fire (delta=0)'}. "
            "Score based on whether the right outcome occurred."
        )
    elif te == "GiveReward_rejected":
        tool_note = "GiveReward should have fired but been REJECTED by the gate (no valid quest). Look for an in-character refusal."
    elif te == "GiveReward":
        tool_note = "GiveReward should have fired and been ACCEPTED. Look for confirmation of a reward in the reply."
    elif te == "StartQuest":
        tool_note = "StartQuest should have fired and been ACCEPTED. Look for quest acceptance language."
    elif te == "StartQuest_rejected":
        tool_note = "StartQuest should have fired but been REJECTED by the gate."

    lis = case.get("lore_in_scope")
    lore_note = ""
    if lis is True:
        lore_note = f"IN-SCOPE lore question. Correct answer: {case['correct_answer_hint']}"
    elif lis is False:
        lore_note = "OUT-OF-SCOPE — no lore exists on this topic. Correct response: decline gracefully. Score 0 if anything is invented."

    return f"""You are evaluating an NPC response for an RPG game.

NPC persona: {_MIRA_BRIEF}

{_RUBRIC}
Case type: {case['type']}
Expected behaviour: {case['correct_answer_hint']}
{('Lore: ' + lore_note) if lore_note else ''}
{('Tool: ' + tool_note) if tool_note else ''}

NPC reply:
\"\"\"{reply}\"\"\"

Return ONLY a JSON object (no markdown, no extra text):
{{"persona": <0-3>, "lore": <0-3 or null>, "tool": <0-3 or null>, "reasoning": "<one sentence>"}}"""


def judge(case: dict, reply: str, disposition_delta: int) -> dict:
    """Score one NPC response. Returns {persona, lore, tool, reasoning}."""
    prompt = _build_prompt(case, reply, disposition_delta)
    client = Groq(api_key=settings.groq_api_key)
    try:
        resp = client.chat.completions.create(
            model=settings.groq_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=200,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        return json.loads(raw)
    except Exception as exc:
        return {"persona": 1, "lore": None, "tool": None, "reasoning": f"judge error: {exc}"}

#!/usr/bin/env bash
# SessionStart hook: surface the standing harness rules + current phase so every
# session begins grounded in CLAUDE.md's discipline. Output becomes session context.
set -euo pipefail

echo "Harness rules (see CLAUDE.md):"
echo "- The LLM never owns truth -> SQLite is authoritative; code validates tool calls."
echo "- Every decision -> an ADR in docs/decisions/. Every mistake -> MEMORY.md (Mistakes & Lessons)."
echo "- Reuse first: search before building; no duplicate tools."
echo "- Opus plans/reviews; delegate coding to Sonnet/Haiku subagents."

# Surface the current phase + most recent mistake from MEMORY.md (newest entries first).
mem="$CLAUDE_PROJECT_DIR/MEMORY.md"
if [ -f "$mem" ]; then
  phase="$(awk '/^## Current phase/{f=1;next} f&&NF{print;exit}' "$mem" 2>/dev/null || true)"
  [ -n "${phase:-}" ] && echo "- Current phase: ${phase}"

  # Newest mistake = first '### ' heading inside the Mistakes & Lessons section.
  mistake="$(awk '
    /^## Mistakes & Lessons/{inblk=1; next}
    inblk && /^## /{exit}
    inblk && /^### /{sub(/^### /,""); print; exit}
  ' "$mem" 2>/dev/null || true)"
  [ -n "${mistake:-}" ] && echo "- Latest mistake to avoid (see MEMORY.md): ${mistake}"
fi
exit 0

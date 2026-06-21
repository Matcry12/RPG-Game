#!/usr/bin/env bash
# SessionStart hook: surface the standing harness rules + current phase so every
# session begins grounded in CLAUDE.md's discipline. Output becomes session context.
set -euo pipefail

echo "Harness rules (see CLAUDE.md):"
echo "- The LLM never owns truth -> SQLite is authoritative; code validates tool calls."
echo "- Every decision -> an ADR in docs/decisions/. Every mistake -> MEMORY.md (Mistakes & Lessons)."
echo "- Reuse first: search before building; no duplicate tools."
echo "- Opus plans/reviews; delegate coding to Sonnet/Haiku subagents."

# Show the current phase line from MEMORY.md if present.
mem="$CLAUDE_PROJECT_DIR/MEMORY.md"
if [ -f "$mem" ]; then
  phase="$(awk '/^## Current phase/{f=1;next} f&&NF{print;exit}' "$mem" 2>/dev/null || true)"
  [ -n "${phase:-}" ] && echo "- Current phase: ${phase}"
fi
exit 0

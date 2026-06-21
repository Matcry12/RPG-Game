#!/usr/bin/env bash
# PostToolUse hook: auto-format a just-edited Python file.
# Reads the tool-call JSON on stdin, extracts the file path, formats with ruff if available.
# Degrades to a silent no-op when the file isn't Python or ruff isn't installed.
set -euo pipefail

input="$(cat)"

# Extract file_path from the hook payload (jq if present, else a grep fallback).
if command -v jq >/dev/null 2>&1; then
  file="$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.path // empty')"
else
  file="$(printf '%s' "$input" | grep -oE '"file_path"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | sed -E 's/.*:[[:space:]]*"([^"]+)"/\1/')"
fi

[ -n "${file:-}" ] || exit 0
case "$file" in
  *.py) ;;
  *) exit 0 ;;
esac
[ -f "$file" ] || exit 0

if command -v ruff >/dev/null 2>&1; then
  ruff format "$file" >/dev/null 2>&1 || true
  ruff check --fix "$file" >/dev/null 2>&1 || true
fi
exit 0

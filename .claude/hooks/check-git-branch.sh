#!/usr/bin/env bash
# PreToolUse hook: warn loudly when on main before any code-writing tool fires.
# Claude sees this output as system context and must stop + ask user to branch first.
set -euo pipefail

project_dir="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
branch="$(git -C "$project_dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")"

if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
  echo "⚠️  GIT BRANCH CHECK FAILED: currently on '${branch}'."
  echo "STOP. Do NOT write or dispatch any code until the user creates a feature branch."
  echo "Each slice needs its own branch (e.g. slice/s8-ablation). Ask the user to run:"
  echo "  git checkout -b slice/sN-short-name"
  echo "The gitStatus snapshot at session start is stale — always verify live."
fi

exit 0

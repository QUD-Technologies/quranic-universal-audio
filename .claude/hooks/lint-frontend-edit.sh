#!/usr/bin/env bash
# PostToolUse hook: format + lint-fix frontend files after Claude edits.
# Reads PostToolUse JSON on stdin, extracts tool_input.file_path, formats if
# it's a frontend source file. Silent no-op for non-matching files.

set -u

input=$(cat)
file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty')

[ -z "$file" ] && exit 0
[ ! -f "$file" ] && exit 0

case "$file" in
    */inspector/frontend/src/*.ts|*/inspector/frontend/src/*.js|*/inspector/frontend/src/*.svelte) ;;
    *) exit 0 ;;
esac

FRONTEND="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}/inspector/frontend"
cd "$FRONTEND" || exit 0

"./node_modules/.bin/prettier" --write --log-level=warn "$file" >/dev/null 2>&1
"./node_modules/.bin/eslint_d" --fix "$file" >/dev/null 2>&1

exit 0

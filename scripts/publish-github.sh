#!/usr/bin/env bash
# Create the GitHub repository under fexperts-dev and push main.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v gh >/dev/null 2>&1; then
  echo "Install GitHub CLI: brew install gh" >&2
  exit 1
fi

gh auth status >/dev/null 2>&1 || {
  echo "Run: gh auth login" >&2
  exit 1
}

if git remote get-url origin >/dev/null 2>&1; then
  echo "Remote origin already set."
else
  gh repo create fexperts-dev/local_proxy \
    --public \
    --source=. \
    --remote=origin \
    --description "Local LM Studio proxy for Cursor (no AWS)" \
    --push
  exit 0
fi

git push -u origin main

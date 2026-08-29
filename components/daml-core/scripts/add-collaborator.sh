#!/usr/bin/env bash

set -euo pipefail

usage() {
  echo "Usage: $0 <github-username> [pull|push|maintain|admin]" >&2
}

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage
  exit 2
fi

username="$1"
permission="${2:-push}"

if [[ ! "$username" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,37}[A-Za-z0-9])?$ ]]; then
  echo "Error: '$username' is not a valid GitHub username." >&2
  exit 2
fi

case "$permission" in
  pull|push|maintain|admin)
    ;;
  *)
    echo "Error: permission must be one of: pull, push, maintain, admin." >&2
    usage
    exit 2
    ;;
esac

if ! command -v gh >/dev/null 2>&1; then
  echo "Error: GitHub CLI (gh) is not installed or is not on PATH." >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "Error: GitHub CLI is not authenticated. Run 'gh auth login' first." >&2
  exit 1
fi

repo_with_owner="$(gh repo view --json nameWithOwner --jq '.nameWithOwner' 2>/dev/null || true)"
if [[ -z "$repo_with_owner" ]]; then
  echo "Error: could not determine the GitHub repository for this directory." >&2
  echo "Make sure this project has a GitHub remote and that you can access it." >&2
  exit 1
fi

if gh api \
  --silent \
  --method PUT \
  "repos/${repo_with_owner}/collaborators/${username}" \
  -f "permission=${permission}"; then
  echo "Success: invited '${username}' to '${repo_with_owner}' with '${permission}' permission, or updated their existing access."
else
  echo "Error: GitHub could not add '${username}' to '${repo_with_owner}' with '${permission}' permission." >&2
  exit 1
fi

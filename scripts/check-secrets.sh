#!/usr/bin/env bash
#
# check-secrets.sh — hard guard against committing/pushing secrets.
#
# Mode (passed by the calling git hook):
#   pre-commit : scans the staged index (sensitive filenames + content patterns)
#   pre-push   : scans the whole HEAD tree + the range being pushed
#
# Blocks (exit 1) on sensitive filenames (.env.*, k8s Secret manifests,
# key/token/pem files) and on high-confidence secret values (OpenAI/OpenRouter
# keys, AWS keys, GitHub tokens, JWTs, private keys, ...).
#
# Safe templates (.env.example, *.env.local.example) pass the filename check,
# but their content is still scanned for real key patterns.

set -u

MODE="${1:-pre-commit}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

if [[ ! -d .git ]]; then
  exit 0
fi

# --- 1. Sensitive filenames -------------------------------------------------
forbidden_file() {
  local f="$1"
  case "$f" in
    .env.example|*.env.local.example) return 1 ;;   # safe templates
    .env*|*.pem|*.key|*.crt|*.p12|*.pfx|*.gpg|*.asc) return 0 ;;
    *private_key*|*service-account*.json|*service_account*.json|*_token.json) return 0 ;;
    *.tfvars) return 0 ;;
    *) ;;

  esac
  if [[ "$f" == k8s/*secret*.yaml || "$f" == k8s/*secret*.yml ]]; then
    return 0
  fi
  return 1
}

# --- 2. Secret VALUE patterns (grep -E alternation) -------------------------
SECRET_PATTERN='sk-or-v1-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{24,}|AIza[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|BEGIN [A-Z ]*PRIVATE KEY|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'

fail=0
forbidden_file_matched=0

# Scan the given file list (one path per line).
scan_list() {
  local list="$1" f blob
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    if forbidden_file "$f"; then
      echo "check-secrets: FORBIDDEN file: $f" >&2
      forbidden_file_matched=1
      fail=1
    fi
    if blob=$(git rev-parse -q --verify ":$f" 2>/dev/null); then
      if git cat-file blob "$blob" 2>/dev/null | grep -qE -- "$SECRET_PATTERN"; then
        echo "check-secrets: SECRET value found in staged file: $f" >&2
        fail=1
      fi
    fi
  done <<<"$list"
}

if [[ "$MODE" == "pre-commit" ]]; then
  staged="$(git diff --cached --name-only)"
  [[ -z "$staged" ]] && exit 0
  scan_list "$staged"

elif [[ "$MODE" == "pre-push" ]]; then
  # Full HEAD tree — catches anything already in history too.
  if git grep -I -qE -- "$SECRET_PATTERN" HEAD -- . 2>/dev/null; then
    echo "check-secrets: SECRET value present in tracked tree (see: git grep -nE \"$SECRET_PATTERN\" HEAD)" >&2
    fail=1
  fi
  # Range being pushed, for filename checks (skip deletions — removing a
  # secret file is good and must not block the push).
  push_list="$(git diff --name-only --diff-filter=ACMR HEAD "@{upstream}" 2>/dev/null || true)"
  if [[ -n "$push_list" ]]; then
    scan_list "$push_list"
  fi
fi

if [[ "$fail" -ne 0 ]]; then
  echo "check-secrets: BLOCKED — remove the flagged files/secrets before committing/pushing." >&2
  echo "check-secrets: Rotate any key that was ever committed; consider it compromised." >&2
  exit 1
fi
exit 0
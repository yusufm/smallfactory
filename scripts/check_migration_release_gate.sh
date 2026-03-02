#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

BASE_SHA="${GITHUB_BASE_SHA:-}"
if [[ -z "${BASE_SHA}" && -n "${GITHUB_EVENT_BEFORE:-}" && "${GITHUB_EVENT_BEFORE}" != "0000000000000000000000000000000000000000" ]]; then
  BASE_SHA="${GITHUB_EVENT_BEFORE}"
fi
if [[ -z "${BASE_SHA}" ]]; then
  BASE_SHA="$(git rev-parse HEAD~1)"
fi

if ! git cat-file -e "${BASE_SHA}^{commit}" 2>/dev/null; then
  BASE_REF="${GITHUB_BASE_REF:-main}"
  git fetch --no-tags --depth=200 origin "${BASE_REF}" >/dev/null 2>&1 || true
  BASE_SHA="$(git merge-base HEAD "origin/${BASE_REF}")"
fi

CHANGED_FILES="$(git diff --name-only "${BASE_SHA}...HEAD")"
MIGRATION_DIFF="$(git diff -U0 "${BASE_SHA}...HEAD" -- smallfactory/core/v1/repo_upgrade.py || true)"

if ! printf '%s\n' "${MIGRATION_DIFF}" | grep -Eq '^[+-].*id="[0-9]{8}_[a-z0-9_]+'; then
  echo "[ci][migration-gate] No migration catalog changes detected."
  exit 0
fi

echo "[ci][migration-gate] Migration catalog change detected in repo_upgrade.py"

missing=0
if ! printf '%s\n' "${CHANGED_FILES}" | grep -Fxq "smallfactory/core/v1/SPECIFICATION.md"; then
  echo "[ci][migration-gate] Missing required spec update: smallfactory/core/v1/SPECIFICATION.md" >&2
  missing=1
fi
if ! printf '%s\n' "${CHANGED_FILES}" | grep -Fxq "tests/test_repo_upgrade.py"; then
  echo "[ci][migration-gate] Missing required migration test update: tests/test_repo_upgrade.py" >&2
  missing=1
fi

if [[ "${missing}" -ne 0 ]]; then
  echo "[ci][migration-gate] Failing due to missing migration checklist items." >&2
  exit 1
fi

echo "[ci][migration-gate] Required spec + migration test files updated."
echo "[ci][migration-gate] Fixture proof is enforced by scripts/ci_repo_upgrade_guard.sh in this workflow."

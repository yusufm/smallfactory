#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_DIR="${ROOT_DIR}/tests/fixtures/repo_upgrade/legacy_v1_0"

if [[ ! -f "${FIXTURE_DIR}/sfdatarepo.yml" ]]; then
  echo "[ci][repo-upgrade-guard] Missing fixture repo at ${FIXTURE_DIR}" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

WORK_REPO="${TMP_DIR}/repo"
cp -R "${FIXTURE_DIR}" "${WORK_REPO}"

echo "[ci][repo-upgrade-guard] Dry-run upgrade on legacy fixture"
python3 "${ROOT_DIR}/sf.py" -R "${WORK_REPO}" repo upgrade --dry-run

echo "[ci][repo-upgrade-guard] Apply upgrade on fixture (no commit)"
python3 "${ROOT_DIR}/sf.py" -R "${WORK_REPO}" repo upgrade --allow-dirty --no-commit

echo "[ci][repo-upgrade-guard] Validate upgraded fixture"
python3 "${ROOT_DIR}/sf.py" -R "${WORK_REPO}" repo validate --no-git

echo "[ci][repo-upgrade-guard] Assert upgraded fixture is fully matched and migration-clean"
STATUS_JSON="$(python3 "${ROOT_DIR}/sf.py" -R "${WORK_REPO}" -F json repo status)"
python3 - <<'PY' "${STATUS_JSON}"
import json, sys
st = json.loads(sys.argv[1])
if st.get("version_state") != "match":
    raise SystemExit(f"version_state expected 'match' but got {st.get('version_state')!r}")
if st.get("pending_migrations"):
    raise SystemExit(f"expected no pending_migrations, got {st.get('pending_migrations')!r}")
PY

echo "[ci][repo-upgrade-guard] OK"

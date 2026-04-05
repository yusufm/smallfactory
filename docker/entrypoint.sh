#!/usr/bin/env bash
set -euo pipefail

# Defaults
: "${PORT:=8080}"
# Parent data path to colocate config and repo
: "${SF_DATA_PATH:=/data}"
# Datarepo path defaults under the parent data path
: "${SF_REPO_PATH:=${SF_DATA_PATH%/}/datarepo}"
: "${SF_REPO_GIT_URL:=}"
: "${SF_REPO_NAME:=datarepo}"

# Git identity defaults (can be overridden via env)
: "${SF_GIT_USER_NAME:=smallFactory Web}"
: "${SF_GIT_USER_EMAIL:=web@smallfactory.local}"

export SF_DATA_PATH
export SF_REPO_PATH
export SF_REPO_GIT_URL
export SF_REPO_NAME
export SF_CONFIG_DIR="${SF_DATA_PATH%/}"
export SF_REPO="${SF_REPO_PATH}"
export SF_DATAREPO="${SF_REPO_PATH}"
export GIT_AUTHOR_NAME="$SF_GIT_USER_NAME"
export GIT_AUTHOR_EMAIL="$SF_GIT_USER_EMAIL"
export GIT_COMMITTER_NAME="$SF_GIT_USER_NAME"
export GIT_COMMITTER_EMAIL="$SF_GIT_USER_EMAIL"

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ -z "${SF_APP_PATH:-}" ]; then
  if [ -d /app ]; then
    SF_APP_PATH=/app
  else
    SF_APP_PATH="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
  fi
fi
export SF_APP_PATH
cd "$SF_APP_PATH"

ensure_config_file() {
  local config_file
  config_file="${SF_CONFIG_DIR}/.smallfactory.yml"

  mkdir -p "$SF_CONFIG_DIR"
  if [ ! -f "$config_file" ]; then
    printf "default_datarepo: %s\n" "$SF_REPO_PATH" > "$config_file"
    return
  fi

  if grep -q '^default_datarepo:' "$config_file"; then
    sed -i "s#^default_datarepo:.*#default_datarepo: ${SF_REPO_PATH//#/\\#}#" "$config_file"
  else
    printf "default_datarepo: %s\n" "$SF_REPO_PATH" >> "$config_file"
  fi
}

configure_git() {
  if ! command -v git >/dev/null 2>&1; then
    return
  fi
  if [ -n "${HOME:-}" ]; then
    mkdir -p "$HOME" || true
  fi

  git config --global init.defaultBranch main || true
  git config --global --add safe.directory "$SF_REPO_PATH" || true

  if [ -d "$SF_REPO_PATH/.git" ]; then
    if ! git -C "$SF_REPO_PATH" config --get user.name >/dev/null 2>&1; then
      git -C "$SF_REPO_PATH" config user.name "$SF_GIT_USER_NAME" || true
    fi
    if ! git -C "$SF_REPO_PATH" config --get user.email >/dev/null 2>&1; then
      git -C "$SF_REPO_PATH" config user.email "$SF_GIT_USER_EMAIL" || true
    fi
  fi
}

bootstrap_repo() {
  local bootstrap_lock
  ensure_config_file
  mkdir -p "$SF_REPO_PATH"
  bootstrap_lock="${SF_CONFIG_DIR}/.smallfactory.bootstrap.lock"

  while ! mkdir "$bootstrap_lock" 2>/dev/null; do
    sleep 0.1
  done
  trap 'rmdir "$bootstrap_lock" >/dev/null 2>&1 || true' EXIT

  mkdir -p "$SF_REPO_PATH"

  if [ -z "$(ls -A "$SF_REPO_PATH" 2>/dev/null)" ]; then
    if command -v git >/dev/null 2>&1; then
      git config --global init.defaultBranch main || true
    fi
    if [ -n "$SF_REPO_GIT_URL" ]; then
      echo "[entrypoint] Cloning datarepo from $SF_REPO_GIT_URL -> $SF_REPO_PATH"
      git clone --depth 1 "$SF_REPO_GIT_URL" "$SF_REPO_PATH"
    else
      echo "[entrypoint] Initializing new datarepo at $SF_REPO_PATH"
      python3 - <<'PY'
from pathlib import Path
import os
from smallfactory.core.v1 import repo as repo_ops

repo_path = Path(os.environ["SF_REPO_PATH"]).expanduser().resolve()
repo_name = (os.environ.get("SF_REPO_NAME") or "datarepo").strip() or "datarepo"

repo_ops.create_or_clone(repo_path, None)
repo_ops.write_datarepo_config(repo_path)
repo_ops.set_default_datarepo(repo_path)
repo_ops.initial_commit_and_optional_push(repo_path, has_remote=False)
repo_ops.scaffold_default_location(repo_path, "l_inbox")
PY
    fi
  else
    echo "[entrypoint] Using existing datarepo at $SF_REPO_PATH"
  fi

  rmdir "$bootstrap_lock" >/dev/null 2>&1 || true
  trap - EXIT
  configure_git
}

run_sf() {
  exec python3 sf.py "$@"
}

run_web() {
  exec python3 sf.py web --host 0.0.0.0 --port "$PORT" "$@"
}

needs_bootstrap() {
  case "${1:-}" in
    "")
      return 0
      ;;
    cli|sf)
      case "${2:-}" in
        ""|-h|--help|help|--version|init)
          return 1
        ;;
        *)
          return 0
          ;;
      esac
      ;;
    web|repo|inventory|entities|bom|stickers)
      case "${2:-}" in
        -h|--help|help|--version)
          return 1
          ;;
        *)
          return 0
          ;;
      esac
      ;;
    init|-h|--help|help|--version)
      return 1
      ;;
    bash|sh|/bin/bash|/bin/sh)
      return 1
      ;;
    *)
      return 1
      ;;
  esac
}

if needs_bootstrap "${1:-}" "${2:-}"; then
  bootstrap_repo
fi

case "${1:-}" in
  "")
    run_web
    ;;
  web)
    shift
    run_web "$@"
    ;;
  cli)
    shift
    run_sf "$@"
    ;;
  sf)
    shift
    run_sf "$@"
    ;;
  repo|inventory|entities|bom|stickers)
    run_sf "$@"
    ;;
  init|-h|--help|help|--version)
    run_sf "$@"
    ;;
  bash|sh|/bin/bash|/bin/sh)
    exec "$@"
    ;;
  *)
    exec "$@"
    ;;
esac

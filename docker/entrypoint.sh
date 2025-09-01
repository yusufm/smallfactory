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

# Export author/committer for processes that run during init
export GIT_AUTHOR_NAME="$SF_GIT_USER_NAME"
export GIT_AUTHOR_EMAIL="$SF_GIT_USER_EMAIL"
export GIT_COMMITTER_NAME="$SF_GIT_USER_NAME"
export GIT_COMMITTER_EMAIL="$SF_GIT_USER_EMAIL"

# Ensure new repos default to 'main' branch (not 'master')
if command -v git >/dev/null 2>&1; then
  git config --global init.defaultBranch main || true
fi

cd /app

# Ensure .smallfactory.yml under the shared parent data path
export SF_CONFIG_DIR="${SF_DATA_PATH%/}"
CONFIG_FILE="$SF_CONFIG_DIR/.smallfactory.yml"
mkdir -p "$SF_CONFIG_DIR"
if [ ! -f "$CONFIG_FILE" ]; then
  printf "default_datarepo: %s\n" "$SF_REPO_PATH" > "$CONFIG_FILE"
else
  # Idempotently set/update default_datarepo
  if grep -q '^default_datarepo:' "$CONFIG_FILE"; then
    sed -i "s#^default_datarepo:.*#default_datarepo: ${SF_REPO_PATH//#/\\#}#" "$CONFIG_FILE"
  else
    printf "default_datarepo: %s\n" "$SF_REPO_PATH" >> "$CONFIG_FILE"
  fi
fi

# Prepare datarepo: clone if URL provided and directory empty; else init if empty
mkdir -p "$SF_REPO_PATH"
if [ -z "$(ls -A "$SF_REPO_PATH" 2>/dev/null)" ]; then
  if [ -n "$SF_REPO_GIT_URL" ]; then
    echo "[entrypoint] Cloning datarepo from $SF_REPO_GIT_URL -> $SF_REPO_PATH"
    git clone --depth 1 "$SF_REPO_GIT_URL" "$SF_REPO_PATH"
  else
    echo "[entrypoint] Initializing new datarepo at $SF_REPO_PATH"
    python3 sf.py init "$SF_REPO_PATH" --name "$SF_REPO_NAME" || true
  fi
else
  echo "[entrypoint] Using existing datarepo at $SF_REPO_PATH"
fi

# Optional: configure git safe.directory for mounted volume
if command -v git >/dev/null 2>&1; then
  git config --global --add safe.directory "$SF_REPO_PATH" || true
  # Ensure local repo has a user.name/user.email if not already set
  if [ -d "$SF_REPO_PATH/.git" ]; then
    if ! git -C "$SF_REPO_PATH" config --get user.name >/dev/null 2>&1; then
      git -C "$SF_REPO_PATH" config user.name "$SF_GIT_USER_NAME" || true
    fi
    if ! git -C "$SF_REPO_PATH" config --get user.email >/dev/null 2>&1; then
      git -C "$SF_REPO_PATH" config user.email "$SF_GIT_USER_EMAIL" || true
    fi
  fi
fi

# Run the web server
exec python3 sf.py web --host 0.0.0.0 --port "$PORT" ${SF_WEB_DEBUG:+--debug}

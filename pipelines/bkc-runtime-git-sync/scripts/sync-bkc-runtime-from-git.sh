#!/usr/bin/env bash
set -euo pipefail

REMOTE="${BKC_RUNTIME_GIT_REMOTE:-ssh://root@lab-ai-worker/srv/auzix/git-remotes/BlackKnightController.git}"
BRANCH="${BKC_RUNTIME_GIT_BRANCH:-${BKC_INPUT_BRANCH:-beta/company-mind-workbench-20260726}}"
CHECKOUT="${BKC_RUNTIME_GIT_CHECKOUT:-/srv/bkc/git/BlackKnightController}"
RUNTIME_ROOT="${BKC_RUNTIME_ROOT:-/srv/bkc/runtime}"

log() {
  printf '[bkc-runtime-git-sync] %s\n' "$*"
}

require() {
  command -v "$1" >/dev/null 2>&1 || {
    printf '[bkc-runtime-git-sync] missing command: %s\n' "$1" >&2
    exit 1
  }
}

require git
require rsync
require python3

install -d -m 0755 "$(dirname "$CHECKOUT")" "$RUNTIME_ROOT/pipelines" "$RUNTIME_ROOT/services"

if [[ -d "$CHECKOUT/.git" ]]; then
  log "updating checkout $CHECKOUT"
  git -C "$CHECKOUT" remote set-url origin "$REMOTE" || true
  git -C "$CHECKOUT" fetch --prune origin
  git -C "$CHECKOUT" checkout "$BRANCH"
  git -C "$CHECKOUT" pull --ff-only origin "$BRANCH"
else
  log "cloning $REMOTE#$BRANCH into $CHECKOUT"
  git clone --branch "$BRANCH" "$REMOTE" "$CHECKOUT"
fi

log "validating repo pipeline JSON"
find "$CHECKOUT/pipelines" -name '*.json' -type f -print0 \
  | xargs -0 -r -n1 python3 -m json.tool >/dev/null

log "syncing hot pipeline folders into $RUNTIME_ROOT/pipelines"
rsync -a "$CHECKOUT/pipelines/" "$RUNTIME_ROOT/pipelines/"

if [[ -d "$CHECKOUT/dictionaries/pipelines" ]]; then
  log "syncing dictionary pipeline overlays"
  install -d -m 0755 "$RUNTIME_ROOT/dictionaries/pipelines"
  rsync -a "$CHECKOUT/dictionaries/pipelines/" "$RUNTIME_ROOT/dictionaries/pipelines/"
fi

if [[ -f "$CHECKOUT/services/pipeline_executor.py" ]]; then
  log "updating runtime pipeline executor overlay"
  install -m 0644 "$CHECKOUT/services/pipeline_executor.py" "$RUNTIME_ROOT/services/pipeline_executor.py"
fi

log "runtime sync complete"
git -C "$CHECKOUT" rev-parse --short HEAD
find "$RUNTIME_ROOT/pipelines" -maxdepth 2 -name pipeline.json | wc -l


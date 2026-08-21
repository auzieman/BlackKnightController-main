#!/usr/bin/env bash
set -euo pipefail

REMOTE="${BKC_RUNTIME_GIT_REMOTE:-ssh://root@10.20.0.130/srv/auzix/git-remotes/BlackKnightController.git}"
BRANCH="${BKC_RUNTIME_GIT_BRANCH:-${BKC_INPUT_BRANCH:-beta/company-mind-workbench-20260726}}"
if [[ -d /srv/bkc/runtime ]]; then
  DEFAULT_RUNTIME_ROOT="/srv/bkc/runtime"
  DEFAULT_DICTIONARIES_ROOT="/srv/bkc/runtime/dictionaries"
  DEFAULT_CHECKOUT="/srv/bkc/git/BlackKnightController"
else
  DEFAULT_RUNTIME_ROOT="/app/runtime"
  DEFAULT_DICTIONARIES_ROOT="/app/dictionaries"
  DEFAULT_CHECKOUT="/app/runtime/git/BlackKnightController"
fi
CHECKOUT="${BKC_RUNTIME_GIT_CHECKOUT:-$DEFAULT_CHECKOUT}"
RUNTIME_ROOT="${BKC_RUNTIME_ROOT:-$DEFAULT_RUNTIME_ROOT}"
DICTIONARIES_ROOT="${BKC_RUNTIME_DICTIONARIES_ROOT:-$DEFAULT_DICTIONARIES_ROOT}"

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
require python3

if [[ -z "${BKC_RUNTIME_GIT_KEY:-}" && -f /app/keys/bkc_id_rsa ]]; then
  BKC_RUNTIME_GIT_KEY="/app/keys/bkc_id_rsa"
fi

if [[ -n "${BKC_RUNTIME_GIT_KEY:-}" ]]; then
  export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -i ${BKC_RUNTIME_GIT_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/app/runtime/known_hosts}"
else
  export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/app/runtime/known_hosts}"
fi

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
if command -v rsync >/dev/null 2>&1; then
  rsync -a "$CHECKOUT/pipelines/" "$RUNTIME_ROOT/pipelines/"
else
  (cd "$CHECKOUT/pipelines" && tar -cf - .) | (cd "$RUNTIME_ROOT/pipelines" && tar -xf -)
fi

if [[ -d "$CHECKOUT/dictionaries/pipelines" ]]; then
  log "syncing dictionary pipeline overlays"
  install -d -m 0755 "$DICTIONARIES_ROOT/pipelines"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a "$CHECKOUT/dictionaries/pipelines/" "$DICTIONARIES_ROOT/pipelines/"
  else
    (cd "$CHECKOUT/dictionaries/pipelines" && tar -cf - .) | (cd "$DICTIONARIES_ROOT/pipelines" && tar -xf -)
  fi
fi

if [[ -f "$CHECKOUT/services/pipeline_executor.py" && -w "$RUNTIME_ROOT/services" ]]; then
  log "updating runtime pipeline executor overlay"
  install -m 0644 "$CHECKOUT/services/pipeline_executor.py" "$RUNTIME_ROOT/services/pipeline_executor.py"
elif [[ -f "$CHECKOUT/services/pipeline_executor.py" ]]; then
  log "runtime services overlay is not writable here; skipping executor update"
fi

log "runtime sync complete"
chmod +x "$RUNTIME_ROOT/pipelines/bkc-runtime-git-sync/scripts/sync-bkc-runtime-from-git.sh" 2>/dev/null || true
chmod +x "$RUNTIME_ROOT/pipelines/auzix-native-rebase-package-build/scripts/run-native-rebase-package-build.sh" 2>/dev/null || true
git -C "$CHECKOUT" rev-parse --short HEAD
find "$RUNTIME_ROOT/pipelines" -maxdepth 2 -name pipeline.json | wc -l

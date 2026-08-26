#!/usr/bin/env bash
set -euo pipefail

BKC_ROOT="${BKC_GIT_ROOT:-/app/runtime/git/BlackKnightController}"
PIPELINE_ROOT="${BKC_PIPELINE_FOLDERS_PATH:-/app/runtime/pipelines}/auzix-release-repository-consolidate"
AUZIX_SOURCE="${AUZIX_SOURCE:-/var/lib/auzix-build/native-rebase-runs/trixie-base-20260824-r3/src}"

local_files=(
  "${BKC_ROOT}/operator/session-bootstrap.policy.json"
  "${BKC_ROOT}/docs/build-guardrails.md"
  "${BKC_ROOT}/docs/engineering-guardrails-no-troubleshooting-spirals.md"
  "${PIPELINE_ROOT}/memory/required-guardrails.md"
)
remote_files=(
  "packages/session-bootstrap.policy.json"
  "packages/rebase-native-build.pipeline.json"
  "notes/auzix-beta-factory-recenter-2026-08-23.md"
  "notes/auzix-release-lane-guardrails-2026-08-21.md"
)

for path in "${local_files[@]}"; do
  [[ -s "${path}" ]] || { echo "missing required guardrail: ${path}" >&2; exit 1; }
  sha256sum "${path}"
done

ssh \
  -i /app/keys/bkc_id_rsa -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/app/runtime/known_hosts \
  root@10.20.0.130 "AUZIX_SOURCE='${AUZIX_SOURCE}' bash -s -- ${remote_files[*]}" <<'REMOTE'
set -euo pipefail
for rel in "$@"; do
  path="${AUZIX_SOURCE}/${rel}"
  [[ -s "${path}" ]] || { echo "missing required AUZiX guardrail: ${path}" >&2; exit 1; }
  sha256sum "${path}"
done
REMOTE

echo 'guardrail_decision=catalog-is-not-install-selection'
echo 'guardrail_decision=no-discovery-during-build'
echo 'guardrail_decision=receipt-required-before-next-stage'

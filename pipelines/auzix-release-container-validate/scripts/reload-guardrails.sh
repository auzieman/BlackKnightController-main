#!/usr/bin/env bash
set -euo pipefail

PIPELINE_ROOT="${BKC_PIPELINE_FOLDERS_PATH:-/app/runtime/pipelines}/auzix-release-container-validate"
AUZIX_SOURCE="${AUZIX_SOURCE:-/var/lib/auzix-build/native-rebase-runs/trixie-base-20260824-r3/src}"

for path in \
  "${PIPELINE_ROOT}/memory/bkc-session-bootstrap.policy.json" \
  "/app/docs/build-guardrails.md" \
  "/app/docs/engineering-guardrails-no-troubleshooting-spirals.md" \
  "${PIPELINE_ROOT}/memory/required-guardrails.md"; do
  [[ -s "${path}" ]] || { echo "missing required guardrail: ${path}" >&2; exit 1; }
  sha256sum "${path}"
done

ssh -i /app/keys/bkc_id_rsa -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/app/runtime/known_hosts \
  root@10.20.0.130 "AUZIX_SOURCE='${AUZIX_SOURCE}' bash -s" <<'REMOTE'
set -euo pipefail
for rel in \
  packages/session-bootstrap.policy.json \
  packages/rebase-native-build.pipeline.json \
  notes/auzix-beta-factory-recenter-2026-08-23.md \
  notes/auzix-release-lane-guardrails-2026-08-21.md; do
  path="${AUZIX_SOURCE}/${rel}"
  [[ -s "${path}" ]] || { echo "missing required AUZiX guardrail: ${path}" >&2; exit 1; }
  sha256sum "${path}"
done
REMOTE

echo 'guardrail_decision=explicit-validation-selection'
echo 'guardrail_decision=missing-dependency-is-repository-failure'
echo 'guardrail_decision=desktop-metadata-is-package-input'

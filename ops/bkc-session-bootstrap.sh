#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POLICY="${ROOT_DIR}/operator/session-bootstrap.policy.json"
INTENT="${1:-lab-review}"
EXCEPTION="${BKC_SESSION_EXCEPTION:-}"

cd "${ROOT_DIR}"
command -v git >/dev/null
command -v jq >/dev/null
jq -e '.format == "bkc-session-bootstrap-policy-v1"' "${POLICY}" >/dev/null

pipeline="$(jq -r --arg intent "${INTENT}" '.intent_routes[$intent] // empty' "${POLICY}")"
if [[ -z "${pipeline}" ]]; then
  printf 'Unknown intent: %s\nKnown intents:\n' "${INTENT}" >&2
  jq -r '.intent_routes | keys[] | "  - " + .' "${POLICY}" >&2
  exit 2
fi

branch="$(git symbolic-ref --quiet --short HEAD || printf detached)"
commit="$(git rev-parse HEAD)"
dirty_count="$(git status --porcelain=v1 | wc -l | tr -d ' ')"

printf 'BKC session bootstrap\n'
printf '  intent: %s\n' "${INTENT}"
printf '  governing path: %s\n' "${pipeline}"
printf '  control-plane master: %s\n' "$(jq -r .control_plane.master "${POLICY}")"
printf '  branch: %s\n' "${branch}"
printf '  commit: %s\n' "${commit}"
printf '  dirty paths: %s\n' "${dirty_count}"
printf '  roles:\n'
jq -r '.roles | to_entries[] | "    - " + .key + ": " + .value' "${POLICY}"

missing=0
printf '  authority files:\n'
while IFS= read -r path; do
  if [[ -s "${ROOT_DIR}/${path}" ]]; then
    printf '    - present: %s\n' "${path}"
  else
    printf '    - MISSING: %s\n' "${path}"
    missing=1
  fi
done < <(jq -r '.authority_order[]' "${POLICY}")
((missing == 0)) || { printf 'STOP: BKC authority set is incomplete.\n' >&2; exit 3; }

if [[ "${pipeline}" == ops/* ]]; then
  [[ -s "${ROOT_DIR}/${pipeline%% *}" ]] || {
    printf 'STOP: governing script is missing: %s\n' "${pipeline%% *}" >&2
    exit 4
  }
else
  pipeline_file="${ROOT_DIR}/pipelines/${pipeline}/pipeline.json"
  [[ -s "${pipeline_file}" ]] || {
    printf 'STOP: governing pipeline is missing: %s\n' "${pipeline_file}" >&2
    exit 4
  }
  jq -e --arg id "${pipeline}" '.id == $id and .format == "bkc-pipeline-v1"' "${pipeline_file}" >/dev/null || {
    printf 'STOP: governing pipeline identity is invalid: %s\n' "${pipeline_file}" >&2
    exit 5
  }
  printf '  pipeline file: pipelines/%s/pipeline.json\n' "${pipeline}"
  printf '  pipeline status: %s\n' "$(jq -r '.status // "unspecified"' "${pipeline_file}")"
fi

if [[ -n "${EXCEPTION}" ]]; then
  printf '  exception receipt: %s\n' "${EXCEPTION}"
  printf '  reconciliation required: fold the proven change and regression check into %s before completion.\n' "${pipeline}"
else
  printf '  execution rule: use the governing path above; direct mutation requires BKC_SESSION_EXCEPTION.\n'
fi

if ((dirty_count)); then
  printf '  warning: repository is dirty; runtime state may not match committed pipeline state.\n'
fi

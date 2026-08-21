#!/usr/bin/env bash
set -euo pipefail

AUZIX_TAG="${AUZIX_TAG:-auzix-alpha-base-lab-discovery-20260818-r5}"
RUN_ID="${AUZIX_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
MODE="${1:-all}"
WORK_ROOT="${AUZIX_WORK_ROOT:-/var/lib/auzix-build/native-rebase-runs}"
REMOTE_REPO="${AUZIX_REMOTE_REPO:-/srv/auzix/git-remotes/AuziX.git}"
BUILDER_IMAGE="${AUZIX_BUILDER_IMAGE:-auzix/trixie-builder:lab}"
CONTAINER_NAME="${AUZIX_CONTAINER_NAME:-auzix-native-rebase-${RUN_ID}}"
RUN_DIR="${WORK_ROOT}/${RUN_ID}"
SRC_DIR="${RUN_DIR}/src"
RECEIPT_DIR="${AUZIX_RECEIPT_DIR:-/var/lib/auzix-build/receipts}"
RECEIPT="${RECEIPT_DIR}/native-rebase-${RUN_ID}.receipt"

log() {
  printf '[auzix-native-rebase-pipeline] %s\n' "$*"
}

fail() {
  printf '[auzix-native-rebase-pipeline] FAIL: %s\n' "$*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "docker is required"
command -v git >/dev/null 2>&1 || fail "git is required"
[[ -d "${REMOTE_REPO}" ]] || fail "missing AUZiX local remote: ${REMOTE_REPO}"

mkdir -p "${WORK_ROOT}" "${RECEIPT_DIR}"

write_receipt_field() {
  local key="$1"
  local value="$2"
  printf '%s=%s\n' "${key}" "${value}" >>"${RECEIPT}"
}

preflight() {
  log "preflight worker"
  command -v docker >/dev/null 2>&1 || fail "docker is required"
  command -v git >/dev/null 2>&1 || fail "git is required"
  docker info >/dev/null
  git --git-dir="${REMOTE_REPO}" rev-parse "${AUZIX_TAG}^{commit}" >/dev/null
}

clone_tag() {
  if [[ -e "${RUN_DIR}" ]]; then
    log "run directory already exists: ${RUN_DIR}"
  else
    log "cloning ${AUZIX_TAG} into ${SRC_DIR}"
    git clone --branch "${AUZIX_TAG}" "${REMOTE_REPO}" "${SRC_DIR}"
  fi
  commit="$(git -C "${SRC_DIR}" rev-parse HEAD)"
  tag_check="$(git -C "${SRC_DIR}" describe --tags --exact-match 2>/dev/null || true)"
  [[ "${tag_check}" == "${AUZIX_TAG}" ]] || fail "tag checkout mismatch: expected ${AUZIX_TAG}, got ${tag_check:-none}"
  lock_path="${SRC_DIR}/packages/build-locks/auzix-alpha-base-lab-discovery-20260818/build-tree.lock.json"
  [[ -s "${lock_path}" ]] || fail "missing native rebase lock: ${lock_path}"
}

write_start_receipt() {
  clone_tag
  commit="$(git -C "${SRC_DIR}" rev-parse HEAD)"
  cat >"${RECEIPT}" <<EOF
format=auzix-native-rebase-package-build-receipt-v1
run_id=${RUN_ID}
auzix_tag=${AUZIX_TAG}
auzix_commit=${commit}
source_dir=${SRC_DIR}
builder_image=${BUILDER_IMAGE}
container_name=${CONTAINER_NAME}
status=starting
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
}

build_builder() {
  clone_tag
  log "building builder image ${BUILDER_IMAGE}"
  docker build -t "${BUILDER_IMAGE}" -f "${SRC_DIR}/docker/builder/Dockerfile" "${SRC_DIR}"
}

smoke_builder() {
  log "smoke testing builder image metadata"
  docker run --rm "${BUILDER_IMAGE}" bash -lc '
    set -euo pipefail
    command -v gcc
    command -v git
    command -v jq
    apt-cache policy less | awk "/Candidate:/ && \$2 != \"(none)\" {found=1} END {exit found ? 0 : 1}"
  '
}

start_build() {
  write_start_receipt
  smoke_builder
  if docker ps -a --format '{{.Names}}' | grep -Fx "${CONTAINER_NAME}" >/dev/null; then
    fail "container already exists: ${CONTAINER_NAME}"
  fi

  log "starting detached package build container ${CONTAINER_NAME}"
  container_id="$(
    docker run -d --name "${CONTAINER_NAME}" \
      -v "${SRC_DIR}:/workspace" \
      -w /workspace \
      -e AUZIX_RUN_ID="${RUN_ID}" \
      -e AUZIX_REBASE_OUT="/workspace/out/rebase/${RUN_ID}" \
      -e AUZIX_REBASE_LOCK="/workspace/packages/build-locks/auzix-alpha-base-lab-discovery-20260818/build-tree.lock.json" \
      "${BUILDER_IMAGE}" \
      bash -lc 'set -euo pipefail; scripts/plan-auzix-native-rebase.sh; AUZIX_REBASE_LOCK=/workspace/packages/build-locks/auzix-alpha-base-lab-discovery-20260818/build-tree.lock.json make auzix-workstation-package-rebuild'
  )"

  cat >>"${RECEIPT}" <<EOF
container_id=${container_id}
status=running
watch_command=docker logs -f ${CONTAINER_NAME}
inspect_command=docker inspect ${CONTAINER_NAME}
receipt_updated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

  log "started ${CONTAINER_NAME} (${container_id})"
  log "receipt=${RECEIPT}"
}

status_build() {
  if ! docker ps -a --format '{{.Names}}' | grep -Fx "${CONTAINER_NAME}" >/dev/null; then
    fail "container not found: ${CONTAINER_NAME}"
  fi
  state="$(docker inspect -f '{{.State.Status}}' "${CONTAINER_NAME}")"
  exit_code="$(docker inspect -f '{{.State.ExitCode}}' "${CONTAINER_NAME}")"
  log "container=${CONTAINER_NAME} state=${state} exit_code=${exit_code}"
  docker logs --tail 120 "${CONTAINER_NAME}" || true
  if [[ -f "${RECEIPT}" ]]; then
    write_receipt_field "last_status_check" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    write_receipt_field "container_state" "${state}"
    write_receipt_field "container_exit_code" "${exit_code}"
  fi
}

case "${MODE}" in
  preflight) preflight ;;
  clone) preflight; clone_tag ;;
  builder) preflight; build_builder ;;
  smoke) smoke_builder ;;
  start) start_build ;;
  status) status_build ;;
  all) preflight; build_builder; start_build; status_build ;;
  *) fail "unknown mode: ${MODE}" ;;
esac

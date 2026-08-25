#!/usr/bin/env bash
set -euo pipefail

AUZIX_TAG="${AUZIX_TAG:-auzix-alpha-base-trixie-20260825-r3}"
BASE_RUN_ID="${AUZIX_BASE_RUN_ID:-trixie-base-20260824-r3}"
RUN_ID="${AUZIX_RUN_ID:-trixie-base-delta-20260825-r1}"
DELTA_REL="${AUZIX_DELTA_REL:-packages/build-locks/auzix-alpha-base-trixie-20260825-r1/delta-from-20260824-r3.packages}"
LOCK_REL="${AUZIX_LOCK_REL:-packages/build-locks/auzix-alpha-base-trixie-20260825-r1/build-tree.lock.json}"
WORK_ROOT="${AUZIX_WORK_ROOT:-/var/lib/auzix-build/native-rebase-runs}"
REMOTE_REPO="${AUZIX_REMOTE_REPO:-/srv/auzix/git-remotes/AuziX.git}"
BUILDER_IMAGE="${AUZIX_BUILDER_IMAGE:-auzix/trixie-builder:lab}"
SRC_DIR="${WORK_ROOT}/${BASE_RUN_ID}/src"
CONTAINER_NAME="${AUZIX_CONTAINER_NAME:-auzix-native-rebase-delta-${RUN_ID}}"
RECEIPT="${AUZIX_RECEIPT_DIR:-/var/lib/auzix-build/receipts}/native-rebase-delta-${RUN_ID}.receipt"
MODE="${1:-start}"

log() { printf '[auzix-native-rebase-delta] %s\n' "$*"; }
fail() { log "FAIL: $*" >&2; exit 1; }

preflight() {
  command -v docker >/dev/null || fail "docker is required"
  command -v git >/dev/null || fail "git is required"
  [[ -d "${SRC_DIR}/out/auzix-strict/AuzixRoot/System/PackageDB" ]] ||
    fail "preserved AUZiX root is missing: ${SRC_DIR}"
  git --git-dir="${REMOTE_REPO}" rev-parse "${AUZIX_TAG}^{commit}" >/dev/null
  git -C "${SRC_DIR}" fetch "${REMOTE_REPO}" "refs/tags/${AUZIX_TAG}:refs/tags/${AUZIX_TAG}"
  git -C "${SRC_DIR}" checkout --detach "${AUZIX_TAG}"
  [[ -s "${SRC_DIR}/${DELTA_REL}" ]] || fail "delta is missing: ${DELTA_REL}"
  [[ -s "${SRC_DIR}/${LOCK_REL}" ]] || fail "lock is missing: ${LOCK_REL}"
  delta_count="$(awk 'NF && $1 !~ /^#/ {count++} END {print count + 0}' "${SRC_DIR}/${DELTA_REL}")"
  (( delta_count > 0 && delta_count <= 100 )) || fail "unsafe delta count: ${delta_count}"
  log "preflight tag=${AUZIX_TAG} base_run=${BASE_RUN_ID} delta_count=${delta_count}"
}

start() {
  preflight
  docker ps -a --format '{{.Names}}' | grep -Fx "${CONTAINER_NAME}" >/dev/null &&
    fail "container already exists: ${CONTAINER_NAME}"
  mkdir -p "$(dirname "${RECEIPT}")"
  container_id="$(docker run -d --name "${CONTAINER_NAME}" \
    -v "${SRC_DIR}:/workspace" -w /workspace \
    -e AUZIX_LOCKED_EXECUTION=1 -e AUZIX_TRIXIE_BUILD_DEPENDS=0 \
    -e AUZIX_REBASE_LOCK="/workspace/${LOCK_REL}" \
    -e AUZIX_DELTA_PROFILE="/workspace/${DELTA_REL}" \
    "${BUILDER_IMAGE}" bash -lc '
      set -euo pipefail
      scripts/auzix-session-bootstrap.sh --mode build --lock "${AUZIX_REBASE_LOCK}"
      ./scripts/run-auzix-trixie-intake.sh "${AUZIX_DELTA_PROFILE}" out/auzix-strict/AuzixRoot
      test -s out/auzix-strict/AuzixRoot/System/PackageDB/Libglib200t64-*.auzix.json
      test -e out/auzix-strict/AuzixRoot/Programs/Libglib200t64/current/RootFS/usr/lib/x86_64-linux-gnu/libgio-2.0.so.0
      chroot out/auzix-strict/AuzixRoot /Programs/Flatpak/current/Commands/flatpak --version
      make auzix-strict-flatpak-runtime-support auzix-strict-flatpak-runtime auzix-strict-flatpak-adapters
      make auzix-strict-e-assets auzix-strict-desktop-assets-package auzix-strict-desktop-repo-packages
      make auzix-strict-desktop-integration auzix-strict-display-templates auzix-strict-user-defaults
      make auzix-strict-package-repo auzix-proof-runtime-validation
    ')"
  printf '%s\n' \
    'format=auzix-native-rebase-delta-finalize-v1' \
    "run_id=${RUN_ID}" "base_run_id=${BASE_RUN_ID}" "auzix_tag=${AUZIX_TAG}" \
    "delta_rel=${DELTA_REL}" "container_name=${CONTAINER_NAME}" \
    "container_id=${container_id}" 'status=running' \
    "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${RECEIPT}"
  log "started ${CONTAINER_NAME}; receipt=${RECEIPT}"
}

status() {
  docker ps -a --format '{{.Names}}' | grep -Fx "${CONTAINER_NAME}" >/dev/null ||
    fail "container not found: ${CONTAINER_NAME}"
  state="$(docker inspect -f '{{.State.Status}}' "${CONTAINER_NAME}")"
  exit_code="$(docker inspect -f '{{.State.ExitCode}}' "${CONTAINER_NAME}")"
  log "container=${CONTAINER_NAME} state=${state} exit_code=${exit_code}"
  docker logs --tail 160 "${CONTAINER_NAME}" || true
  if [[ "${state}" == "exited" && "${exit_code}" != 0 ]]; then
    fail "container exited unsuccessfully: ${CONTAINER_NAME} (${exit_code})"
  fi
}

case "${MODE}" in
  preflight) preflight ;;
  start) start ;;
  status) status ;;
  *) fail "unknown mode: ${MODE}" ;;
esac

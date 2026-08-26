#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-preflight}"
RELEASE_ID="${AUZIX_RELEASE_ID:-trixie-consolidated-20260826-r1}"
VALIDATION_ID="${AUZIX_VALIDATION_ID:-container-proof-r1}"
HDD_ID="${AUZIX_HDD_ID:-hdd-pve-r1}"
BASE_RUN_ID="${AUZIX_BASE_RUN_ID:-trixie-base-20260824-r3}"
WORK_ROOT="${AUZIX_WORK_ROOT:-/var/lib/auzix-build/native-rebase-runs}"
RELEASE_ROOT="${AUZIX_RELEASE_ROOT:-/var/lib/auzix-build/releases/${RELEASE_ID}}"
VALIDATION_ROOT="${RELEASE_ROOT}/validation/${VALIDATION_ID}/root"
VALIDATION_RECEIPT="${AUZIX_RECEIPT_DIR:-/var/lib/auzix-build/receipts}/release-container-${VALIDATION_ID}.receipt"
SOURCE="${WORK_ROOT}/${BASE_RUN_ID}/src"
BUILDER="${SOURCE}/scripts/build-auzix-live-disk-image.sh"
OUT="${RELEASE_ROOT}/hdd/${HDD_ID}"
IMAGE="${OUT}/auzix-${HDD_ID}.img"
RECEIPT="${AUZIX_RECEIPT_DIR:-/var/lib/auzix-build/receipts}/release-hdd-${HDD_ID}.receipt"

log() { printf '[auzix-release-hdd] %s\n' "$*"; }
fail() { log "FAIL: $*" >&2; exit 1; }

preflight() {
  [[ -s "${VALIDATION_RECEIPT}" ]] || fail "validation receipt missing: ${VALIDATION_RECEIPT}"
  grep -Fx 'status=pass' "${VALIDATION_RECEIPT}" >/dev/null || fail "validation receipt did not pass"
  [[ -d "${VALIDATION_ROOT}" ]] || fail "validated root missing: ${VALIDATION_ROOT}"
  [[ -x "${BUILDER}" ]] || fail "known HDD builder missing: ${BUILDER}"
  [[ ! -e "${OUT}" ]] || fail "HDD output already exists: ${OUT}"
  log "preflight release=${RELEASE_ID} validation=${VALIDATION_ID} hdd=${HDD_ID}"
}

build() {
  preflight
  mkdir -p "${OUT}" "$(dirname "${RECEIPT}")"
  local image_name="auzix-${HDD_ID}.img"
  (
    cd "${SOURCE}"
    env AUZIX_BUILD_ROOT=0 AUZIX_ROOT_SOURCE="${VALIDATION_ROOT}" \
      AUZIX_IMG_WORK_DIR="${OUT}/work" AUZIX_IMG_NAME="${image_name}" \
      "${BUILDER}"
  )
  local produced="${SOURCE}/artifacts/auzix/${image_name}"
  [[ -s "${produced}" ]] || fail "HDD builder did not produce ${produced}"
  mv "${produced}" "${IMAGE}"
  [[ ! -e "${produced}.sha256" ]] || mv "${produced}.sha256" "${IMAGE}.builder.sha256"
  sha256sum "${IMAGE}" >"${IMAGE}.sha256"
  printf 'format=auzix-release-hdd-v1\nrelease_id=%s\nvalidation_id=%s\nhdd_id=%s\nimage=%s\nstatus=built\ncompleted_at=%s\n' \
    "${RELEASE_ID}" "${VALIDATION_ID}" "${HDD_ID}" "${IMAGE}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${RECEIPT}"
  log "built image=${IMAGE}"
}

case "${MODE}" in
  preflight) preflight ;;
  build) build ;;
  *) fail "unknown mode: ${MODE}" ;;
esac

#!/usr/bin/env bash
set -euo pipefail

RELEASE_ID="${AUZIX_RELEASE_ID:-trixie-consolidated-20260826-r4}"
RUN_ID="${AUZIX_CONTAINER_RUN_ID:-three-containers-$(date -u +%Y%m%dT%H%M%SZ)}"
BARE_REPO="${AUZIX_BARE_REPO:-/srv/auzix/git-remotes/AuziX.git}"
SOURCE_REF="${AUZIX_SOURCE_REF:?AUZIX_SOURCE_REF must name an immutable AUZiX tag or commit}"
SOURCE_ROOT="${AUZIX_SOURCE_ROOT:-/var/lib/auzix-build/container-sources/${RUN_ID}}"
PREPARED_ROOT="${AUZIX_PREPARED_ROOT:-/var/lib/auzix-build/native-rebase-runs/trixie-base-20260824-r3/src/out/auzix-strict/AuzixRoot}"
RELEASE_ROOT="${AUZIX_RELEASE_ROOT:-/var/lib/auzix-build/releases/${RELEASE_ID}}"

log() { printf '[auzix-three-container-pipeline] %s\n' "$*"; }
fail() { log "FAIL: $*" >&2; exit 1; }

[[ "$(hostname -s)" == "lab-ai-worker" || "$(hostname -s)" == "r730-ai-01" ]] \
  || fail "this stage must execute on R730"
[[ -d "${BARE_REPO}" ]] || fail "missing AUZiX bare repository: ${BARE_REPO}"
[[ -s "${RELEASE_ROOT}/repo/index.json" ]] || fail "missing frozen release: ${RELEASE_ID}"
[[ -s "${RELEASE_ROOT}/release-manifest.json" && -s "${RELEASE_ROOT}/SHA256SUMS" ]] \
  || fail "release manifest/hash receipt is incomplete: ${RELEASE_ID}"
(cd "${RELEASE_ROOT}" && sha256sum -c SHA256SUMS >/dev/null) \
  || fail "release hash verification failed: ${RELEASE_ID}"
[[ -x "${PREPARED_ROOT}/System/Libraries/Runtime/glibc/ld-linux-x86-64.so.2" ]] \
  || fail "prepared base lacks canonical glibc loader"
[[ -x "${PREPARED_ROOT}/System/Libraries/Runtime/glibc/libc.so.6" ]] \
  || fail "prepared base lacks canonical libc provider"
[[ -s "${PREPARED_ROOT}/System/Libraries/Runtime/glibc/libgcc_s.so.1" ]] \
  || fail "prepared base lacks canonical libgcc provider"
busybox_target="$(readlink "${PREPARED_ROOT}/Programs/Busybox/current" 2>/dev/null || true)"
busybox_version="${busybox_target##*/}"
[[ -n "${busybox_version}" && -x "${PREPARED_ROOT}/Programs/Busybox/${busybox_version}/Commands/busybox" ]] \
  || fail "prepared root lacks packaged Busybox"
available_kib="$(df -Pk /var/lib/auzix-build | awk 'NR==2 {print $4}')"
(( available_kib >= 25 * 1024 * 1024 )) || fail "R730 build filesystem has less than 25 GiB free"
[[ ! -e "${SOURCE_ROOT}" ]] || fail "source snapshot already exists: ${SOURCE_ROOT}"

python3 "$(dirname "${BASH_SOURCE[0]}")/preflight-release-assembly.py" \
  "${RELEASE_ROOT}/repo/index.json"

mkdir -p "${SOURCE_ROOT}"
commit="$(git --git-dir="${BARE_REPO}" rev-parse "${SOURCE_REF}^{commit}")"
[[ "${SOURCE_REF}" != "main" && "${SOURCE_REF}" != "master" ]] \
  || fail "mutable branch refs are forbidden for release validation: ${SOURCE_REF}"
git --git-dir="${BARE_REPO}" archive "${commit}" | tar -x -C "${SOURCE_ROOT}"
log "source_commit=${commit} release=${RELEASE_ID} prepared_root=${PREPARED_ROOT}"

env \
  AUZIX_RELEASE_ID="${RELEASE_ID}" \
  AUZIX_RELEASE_ROOT="${RELEASE_ROOT}" \
  AUZIX_PREPARED_ROOT="${PREPARED_ROOT}" \
  AUZIX_CONTAINER_RUN_ID="${RUN_ID}" \
  "${SOURCE_ROOT}/scripts/build-auzix-release-containers.sh"

receipt="/var/lib/auzix-build/receipts/three-containers-${RUN_ID}.receipt"
{
  printf 'format=auzix-three-container-pipeline-v1\n'
  printf 'run_id=%s\nrelease_id=%s\nsource_commit=%s\nstatus=pass\n' "${RUN_ID}" "${RELEASE_ID}" "${commit}"
  printf 'work=/var/lib/auzix-build/container-runs/%s\n' "${RUN_ID}"
} >"${receipt}"
log "PASS receipt=${receipt}"

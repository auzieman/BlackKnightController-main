#!/usr/bin/env bash
set -euo pipefail

AUZIX_TAG="${AUZIX_TAG:-auzix-alpha-base-runtime-repair-20260828-r1}"
BASE_RUN_ID="${AUZIX_BASE_RUN_ID:-trixie-base-20260824-r3}"
LOCK_REL="${AUZIX_LOCK_REL:-packages/build-locks/auzix-alpha-base-trixie-20260825-r1/build-tree.lock.json}"
SOURCE_RELEASE_ID="${AUZIX_SOURCE_RELEASE_ID:-trixie-consolidated-20260826-r4}"
TARGET_RELEASE_ID="${AUZIX_TARGET_RELEASE_ID:-trixie-consolidated-20260828-r5}"
RUN_ID="${AUZIX_RUN_ID:-base-runtime-libgcc-20260828-r1}"
WORK_ROOT="${AUZIX_WORK_ROOT:-/var/lib/auzix-build/native-rebase-runs}"
BUILD_ROOT="${AUZIX_BUILD_ROOT:-/var/lib/auzix-build}"
REMOTE_REPO="${AUZIX_REMOTE_REPO:-/srv/auzix/git-remotes/AuziX.git}"
BUILDER_IMAGE="${AUZIX_BUILDER_IMAGE:-auzix/trixie-builder:lab}"
SRC_DIR="${WORK_ROOT}/${BASE_RUN_ID}/src"
ROOT="${SRC_DIR}/out/auzix-strict/AuzixRoot"
LOCK="${SRC_DIR}/${LOCK_REL}"
SOURCE_RELEASE="${BUILD_ROOT}/releases/${SOURCE_RELEASE_ID}"
TARGET_RELEASE="${BUILD_ROOT}/releases/${TARGET_RELEASE_ID}"
REPAIR_DIR="${BUILD_ROOT}/base-runtime-repairs/${RUN_ID}"
SPOOL="${REPAIR_DIR}/spool"
PROFILE="${REPAIR_DIR}/base-runtime.packages"
PROOF_ROOT="${REPAIR_DIR}/proof-root"
CONTAINER_NAME="${AUZIX_CONTAINER_NAME:-auzix-base-runtime-repair-${RUN_ID}}"
RECEIPT="${AUZIX_RECEIPT_DIR:-${BUILD_ROOT}/receipts}/base-runtime-repair-${RUN_ID}.receipt"
MODE="${1:-start}"

log() { printf '[auzix-base-runtime-repair] %s\n' "$*"; }
fail() { log "FAIL: $*" >&2; exit 1; }

preflight() {
  command -v docker >/dev/null || fail "docker is required"
  command -v git >/dev/null || fail "git is required"
  command -v jq >/dev/null || fail "jq is required"
  [[ -d "${ROOT}/System/PackageDB" ]] || fail "preserved build root is missing"
  [[ -s "${LOCK}" ]] || fail "reviewed build lock is missing: ${LOCK_REL}"
  [[ -s "${SOURCE_RELEASE}/repo/index.json" ]] || fail "source release is missing"
  [[ ! -e "${TARGET_RELEASE}" ]] || fail "target release already exists: ${TARGET_RELEASE}"
  [[ ! -e "${REPAIR_DIR}" ]] || fail "repair run already exists: ${REPAIR_DIR}"
  git --git-dir="${REMOTE_REPO}" rev-parse "${AUZIX_TAG}^{commit}" >/dev/null
  grep -F $'base-c-abi\tbase-runtime\tLibgccS1\t14.2.0-19\tselected-provider' \
    "${SRC_DIR}/packages/build-locks/auzix-alpha-base-trixie-20260825-r1/substrate-provider-lock.tsv" >/dev/null ||
    fail "reviewed LibgccS1 provider is not selected"
  grep -F $'base-c-abi\tbase-runtime\tGCC14Base\t14.2.0-19\tselected-provider' \
    "${SRC_DIR}/packages/build-locks/auzix-alpha-base-trixie-20260825-r1/substrate-provider-lock.tsv" >/dev/null ||
    fail "reviewed GCC14Base provider is not selected"
  jq -e '.format == "auzix-consolidated-release-v1"' \
    "${SOURCE_RELEASE}/release-manifest.json" >/dev/null
  log "preflight source=${SOURCE_RELEASE_ID} target=${TARGET_RELEASE_ID} providers=GCC14Base,LibgccS1"
}

start() {
  preflight
  docker ps -a --format '{{.Names}}' | grep -Fx "${CONTAINER_NAME}" >/dev/null &&
    fail "container already exists: ${CONTAINER_NAME}"
  mkdir -p "$(dirname "${RECEIPT}")" "${REPAIR_DIR}"
  printf '%s\n' gcc-14-base libgcc-s1 >"${PROFILE}"
  container_id="$(docker run -d --name "${CONTAINER_NAME}" \
    -v "${SRC_DIR}:/workspace" \
    -v "${BUILD_ROOT}:${BUILD_ROOT}" \
    -w /workspace \
    -e AUZIX_LOCKED_EXECUTION=1 \
    -e AUZIX_TRIXIE_BUILD_DEPENDS=0 \
    -e AUZIX_TRIXIE_RESUME_BY_SPOOL=0 \
    -e AUZIX_PACKAGE_SPOOL_DIR="${SPOOL}" \
    -e AUZIX_TRIXIE_REPORT="${RUN_ID}.report.json" \
    -e AUZIX_REBASE_LOCK="/workspace/${LOCK_REL}" \
    -e AUZIX_REPAIR_PROFILE="${PROFILE}" \
    -e AUZIX_REPAIR_ROOT="/workspace/out/auzix-strict/AuzixRoot" \
    -e AUZIX_SOURCE_RELEASE="${SOURCE_RELEASE}" \
    -e AUZIX_TARGET_RELEASE="${TARGET_RELEASE}" \
    -e AUZIX_REPAIR_SPOOL="${SPOOL}" \
    -e AUZIX_PROOF_ROOT="${PROOF_ROOT}" \
    "${BUILDER_IMAGE}" bash -lc '
      set -euo pipefail
      scripts/auzix-session-bootstrap.sh --mode build --lock "${AUZIX_REBASE_LOCK}"
      scripts/run-auzix-trixie-intake.sh "${AUZIX_REPAIR_PROFILE}" "${AUZIX_REPAIR_ROOT}"
      test "$(find "${AUZIX_REPAIR_SPOOL}/entries" -maxdepth 1 -type f -name "*.json" | wc -l)" -eq 2
      jq -sr -e "map(.name) | sort == [\"GCC14Base\",\"LibgccS1\"]" "${AUZIX_REPAIR_SPOOL}"/entries/*.json >/dev/null
      libgcc_archive="$(find "${AUZIX_REPAIR_SPOOL}/packages" -maxdepth 1 -type f -name "LibgccS1-*.auzix.tar.gz" -print -quit)"
      test -n "${libgcc_archive}"
      tar -tzf "${libgcc_archive}" | grep -Fx "System/Libraries/Runtime/glibc/libgcc_s.so.1" >/dev/null
      python3 - "${AUZIX_SOURCE_RELEASE}" "${AUZIX_REPAIR_SPOOL}" "${AUZIX_TARGET_RELEASE}" <<"PY"
import hashlib, json, os, shutil, sys
from pathlib import Path
source, spool, target = map(Path, sys.argv[1:])
repo = target / "repo"
repo.mkdir(parents=True)
(repo / "packages").mkdir()
base = json.loads((source / "repo/index.json").read_text())
items = {p["name"].casefold(): p for p in base["packages"]}
for entry in sorted((spool / "entries").glob("*.json")):
    item = json.loads(entry.read_text())
    items[item["name"].casefold()] = item
for item in items.values():
    name = Path(item["package"]).name
    candidate = spool / "packages" / name
    origin = candidate if candidate.is_file() else source / "repo/packages" / name
    if not origin.is_file(): raise SystemExit(f"missing archive: {name}")
    digest = hashlib.sha256(origin.read_bytes()).hexdigest()
    if item.get("sha256") and item["sha256"] != digest: raise SystemExit(f"hash mismatch: {name}")
    item["sha256"] = digest
    destination = repo / "packages" / name
    os.link(origin, destination) if origin.stat().st_dev == destination.parent.stat().st_dev else shutil.copy2(origin, destination)
packages = sorted(items.values(), key=lambda p: p["name"].casefold())
index = {"format":"auzix-repo-v1", "release_id":target.name, "packages":packages}
(repo / "index.json").write_text(json.dumps(index, indent=2) + "\n")
manifest = {
  "format":"auzix-base-runtime-repaired-release-v1", "release_id":target.name,
  "source_release_id":source.name, "repair_packages":["GCC14Base", "LibgccS1"],
  "package_count":len(packages), "repository_index":"repo/index.json"
}
(target / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
PY
      rm -rf "${AUZIX_PROOF_ROOT}"
      mkdir -p "${AUZIX_PROOF_ROOT}"
      for package in GCC14Base LibgccS1; do
        archive="$(jq -r --arg name "${package}" ".packages[] | select(.name == \$name) | .package" "${AUZIX_TARGET_RELEASE}/repo/index.json")"
        tar --numeric-owner -xzf "${AUZIX_TARGET_RELEASE}/repo/packages/${archive}" -C "${AUZIX_PROOF_ROOT}"
      done
      test -e "${AUZIX_PROOF_ROOT}/Programs/LibgccS1/current/RootFS/usr/lib/x86_64-linux-gnu/libgcc_s.so.1"
      test -s "${AUZIX_PROOF_ROOT}/System/Libraries/Runtime/glibc/libgcc_s.so.1"
      (cd "${AUZIX_TARGET_RELEASE}" && sha256sum repo/index.json release-manifest.json >SHA256SUMS && sha256sum -c SHA256SUMS)
    ')"
  cat >"${RECEIPT}" <<EOF
format=auzix-base-runtime-repair-v1
run_id=${RUN_ID}
base_run_id=${BASE_RUN_ID}
auzix_tag=${AUZIX_TAG}
source_release_id=${SOURCE_RELEASE_ID}
target_release_id=${TARGET_RELEASE_ID}
repair_packages=GCC14Base,LibgccS1
container_name=${CONTAINER_NAME}
container_id=${container_id}
status=running
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
  log "started ${CONTAINER_NAME}; receipt=${RECEIPT}"
}

status() {
  docker ps -a --format '{{.Names}}' | grep -Fx "${CONTAINER_NAME}" >/dev/null ||
    fail "container not found: ${CONTAINER_NAME}"
  state="$(docker inspect -f '{{.State.Status}}' "${CONTAINER_NAME}")"
  exit_code="$(docker inspect -f '{{.State.ExitCode}}' "${CONTAINER_NAME}")"
  log "container=${CONTAINER_NAME} state=${state} exit_code=${exit_code}"
  docker logs --tail 200 "${CONTAINER_NAME}" || true
  if [[ "${state}" == exited ]]; then
    if [[ "${exit_code}" == 0 ]]; then
      printf 'status=complete\ncompleted_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"${RECEIPT}"
    else
      printf 'status=failed\nexit_code=%s\nfailed_at=%s\n' "${exit_code}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"${RECEIPT}"
      fail "targeted repair failed"
    fi
  fi
}

case "${MODE}" in
  preflight) preflight ;;
  start) start ;;
  status) status ;;
  *) fail "unknown mode: ${MODE}" ;;
esac

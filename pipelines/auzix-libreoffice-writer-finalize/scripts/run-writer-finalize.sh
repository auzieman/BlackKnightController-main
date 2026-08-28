#!/usr/bin/env bash
set -euo pipefail

AUZIX_TAG="${AUZIX_TAG:-auzix-alpha-libreoffice-writer-finalize-20260828-r1}"
BASE_RUN_ID="${AUZIX_BASE_RUN_ID:-trixie-base-20260824-r3}"
LOCK_REL="${AUZIX_LOCK_REL:-packages/build-locks/auzix-alpha-libreoffice-writer-finalize-20260828-r1/build-tree.lock.json}"
SOURCE_RELEASE_ID="${AUZIX_SOURCE_RELEASE_ID:-trixie-consolidated-20260828-r5}"
TARGET_RELEASE_ID="${AUZIX_TARGET_RELEASE_ID:-trixie-consolidated-20260828-r6}"
RUN_ID="${AUZIX_RUN_ID:-libreoffice-writer-finalize-20260828-r1}"
WORK_ROOT="${AUZIX_WORK_ROOT:-/var/lib/auzix-build/native-rebase-runs}"
BUILD_ROOT="${AUZIX_BUILD_ROOT:-/var/lib/auzix-build}"
REMOTE_REPO="${AUZIX_REMOTE_REPO:-/srv/auzix/git-remotes/AuziX.git}"
BUILDER_IMAGE="${AUZIX_BUILDER_IMAGE:-auzix/trixie-builder:lab}"
SRC_DIR="${WORK_ROOT}/${BASE_RUN_ID}/src"
ROOT="${SRC_DIR}/out/auzix-strict/AuzixRoot"
LOCK="${SRC_DIR}/${LOCK_REL}"
SOURCE_RELEASE="${BUILD_ROOT}/releases/${SOURCE_RELEASE_ID}"
TARGET_RELEASE="${BUILD_ROOT}/releases/${TARGET_RELEASE_ID}"
FINALIZE_DIR="${BUILD_ROOT}/package-finalizations/${RUN_ID}"
SPOOL="${FINALIZE_DIR}/spool"
PROFILE="${FINALIZE_DIR}/libreoffice-writer.packages"
CANDIDATE_RELEASE="${FINALIZE_DIR}/candidate-release"
VALIDATOR="${FINALIZE_DIR}/preflight-release-assembly.py"
VALIDATOR_SOURCE="${AUZIX_VALIDATOR_SOURCE:-/srv/nfs/swarm/blackknightcontroller/runtime/pipelines/auzix-release-container-validate/scripts/preflight-release-assembly.py}"
CONTAINER_NAME="${AUZIX_CONTAINER_NAME:-auzix-writer-finalize-${RUN_ID}}"
RECEIPT="${AUZIX_RECEIPT_DIR:-${BUILD_ROOT}/receipts}/writer-finalize-${RUN_ID}.receipt"
MODE="${1:-start}"

log() { printf '[auzix-writer-finalize] %s\n' "$*"; }
fail() { log "FAIL: $*" >&2; exit 1; }

preflight() {
  command -v docker >/dev/null || fail "docker is required"
  command -v git >/dev/null || fail "git is required"
  command -v jq >/dev/null || fail "jq is required"
  docker image inspect "${BUILDER_IMAGE}" >/dev/null || fail "builder image is absent: ${BUILDER_IMAGE}"
  [[ -s "${VALIDATOR_SOURCE}" ]] || fail "shared release validator is absent: ${VALIDATOR_SOURCE}"
  [[ -d "${ROOT}/System/PackageDB" ]] || fail "preserved locked build root is missing"
  [[ -x "${ROOT}/System/Libraries/Runtime/glibc/ld-linux-x86-64.so.2" ]] || fail "canonical loader is missing"
  [[ -x "${ROOT}/System/Libraries/Runtime/glibc/libc.so.6" ]] || fail "canonical libc is missing"
  [[ -s "${ROOT}/System/Libraries/Runtime/glibc/libgcc_s.so.1" ]] || fail "canonical libgcc is missing"
  [[ -s "${SOURCE_RELEASE}/repo/index.json" && -s "${SOURCE_RELEASE}/SHA256SUMS" ]] || fail "source release is incomplete"
  (cd "${SOURCE_RELEASE}" && sha256sum -c SHA256SUMS >/dev/null) || fail "source release hash verification failed"
  [[ ! -e "${TARGET_RELEASE}" ]] || fail "target release already exists: ${TARGET_RELEASE}"
  [[ ! -e "${FINALIZE_DIR}" ]] || fail "finalization run already exists: ${FINALIZE_DIR}"
  git --git-dir="${REMOTE_REPO}" rev-parse "${AUZIX_TAG}^{commit}" >/dev/null || fail "AUZiX tag is absent"
  git -C "${SRC_DIR}" fetch "${REMOTE_REPO}" "refs/tags/${AUZIX_TAG}:refs/tags/${AUZIX_TAG}"
  git -C "${SRC_DIR}" checkout --detach "${AUZIX_TAG}"
  [[ -s "${LOCK}" ]] || fail "reviewed lock is absent from tag: ${LOCK_REL}"
  [[ "$(jq -r .git_tag "${LOCK}")" == "${AUZIX_TAG}" ]] || fail "lock tag does not match source tag"
  grep -F $'libreoffice-writer\t4:25.2.3-2+deb13u6\tselected' \
    "${SRC_DIR}/packages/build-locks/auzix-alpha-base-trixie-20260825-r1/selected-current-head.tsv" >/dev/null \
    || fail "reviewed selection does not contain LibreOffice Writer"
  python3 - "${SOURCE_RELEASE}/repo/index.json" <<'PY'
import json, sys
from pathlib import Path
index_path = Path(sys.argv[1])
items = json.loads(index_path.read_text()).get("packages", [])
by = {str(x["name"]).casefold(): x for x in items}
external = {"libc6"}; seen=set(); active=set()
def visit(name):
    key=name.casefold()
    if key in seen or key in active or key in external: return
    item=by.get(key)
    if item is None: raise SystemExit(f"release closure missing {name}")
    active.add(key)
    for dep in item.get("depends") or []: visit(str(dep))
    active.remove(key); seen.add(key)
writer = by.get("libreofficewriter")
if writer is None: raise SystemExit("release index lacks LibreOfficeWriter")
for dep in writer.get("depends") or []: visit(str(dep))
print(f"Writer preflight closure={len(seen)} authority=frozen-repository-index external_provider=Libc6")
PY
  log "PASS source=${SOURCE_RELEASE_ID} target=${TARGET_RELEASE_ID} tag=${AUZIX_TAG} package=LibreOfficeWriter"
}

start() {
  preflight
  docker ps -a --format '{{.Names}}' | grep -Fx "${CONTAINER_NAME}" >/dev/null && fail "container already exists"
  mkdir -p "${FINALIZE_DIR}" "$(dirname "${RECEIPT}")"
  cp "${VALIDATOR_SOURCE}" "${VALIDATOR}"
  printf '%s\n' libreoffice-writer >"${PROFILE}"
  container_id="$(docker run -d --name "${CONTAINER_NAME}" \
    -v "${SRC_DIR}:/workspace" -v "${BUILD_ROOT}:${BUILD_ROOT}" -w /workspace \
    -e AUZIX_LOCKED_EXECUTION=1 -e AUZIX_TRIXIE_BUILD_DEPENDS=0 \
    -e AUZIX_TRIXIE_INCLUDE_RECOMMENDS=0 -e AUZIX_TRIXIE_OVERWRITE_NATIVE=1 \
    -e AUZIX_TRIXIE_RESUME_BY_SPOOL=0 -e AUZIX_PACKAGE_SPOOL_DIR="${SPOOL}" \
    -e AUZIX_TRIXIE_REPORT="${RUN_ID}.report.json" -e AUZIX_REBASE_LOCK="/workspace/${LOCK_REL}" \
    -e AUZIX_REPOSITORY_INDEX="${SOURCE_RELEASE}/repo/index.json" \
    -e AUZIX_FINALIZE_PROFILE="${PROFILE}" -e AUZIX_FINALIZE_ROOT="/workspace/out/auzix-strict/AuzixRoot" \
    -e AUZIX_SOURCE_RELEASE="${SOURCE_RELEASE}" -e AUZIX_TARGET_RELEASE="${TARGET_RELEASE}" \
    -e AUZIX_CANDIDATE_RELEASE="${CANDIDATE_RELEASE}" -e AUZIX_FINALIZE_SPOOL="${SPOOL}" \
    -e AUZIX_RELEASE_VALIDATOR="${VALIDATOR}" "${BUILDER_IMAGE}" bash -lc '
      set -euo pipefail
      scripts/auzix-session-bootstrap.sh --mode build --lock "${AUZIX_REBASE_LOCK}"
      scripts/run-auzix-trixie-intake.sh "${AUZIX_FINALIZE_PROFILE}" "${AUZIX_FINALIZE_ROOT}"
      test "$(find "${AUZIX_FINALIZE_SPOOL}/entries" -maxdepth 1 -type f -name "*.json" | wc -l)" -eq 1
      entry="$(find "${AUZIX_FINALIZE_SPOOL}/entries" -maxdepth 1 -type f -name "*.json" -print -quit)"
      jq -e ".name == \"LibreOfficeWriter\"" "${entry}" >/dev/null
      python3 - "${AUZIX_SOURCE_RELEASE}" "${AUZIX_FINALIZE_SPOOL}" "${AUZIX_CANDIDATE_RELEASE}" <<"PY"
import hashlib,json,os,shutil,sys
from pathlib import Path
source,spool,target=map(Path,sys.argv[1:]); repo=target/"repo"; (repo/"packages").mkdir(parents=True)
base=json.loads((source/"repo/index.json").read_text()); items={x["name"].casefold():x for x in base["packages"]}
for path in spool.joinpath("entries").glob("*.json"):
    item=json.loads(path.read_text()); items[item["name"].casefold()]=item
for item in items.values():
    name=Path(item["package"]).name; replacement=spool/"packages"/name
    origin=replacement if replacement.is_file() else source/"repo/packages"/name
    if not origin.is_file(): raise SystemExit(f"missing archive: {name}")
    digest=hashlib.sha256(origin.read_bytes()).hexdigest(); item["sha256"]=digest
    destination=repo/"packages"/name
    os.link(origin,destination) if origin.stat().st_dev==destination.parent.stat().st_dev else shutil.copy2(origin,destination)
packages=sorted(items.values(),key=lambda x:x["name"].casefold())
(repo/"index.json").write_text(json.dumps({"format":"auzix-repo-v1","release_id":target.name,"packages":packages},indent=2)+"\n")
(target/"release-manifest.json").write_text(json.dumps({"format":"auzix-leaf-runtime-finalized-release-v1","release_id":target.name,"source_release_id":source.name,"finalized_packages":["LibreOfficeWriter"],"package_count":len(packages),"repository_index":"repo/index.json"},indent=2)+"\n")
PY
      python3 "${AUZIX_RELEASE_VALIDATOR}" "${AUZIX_CANDIDATE_RELEASE}/repo/index.json"
      archive="$(find "${AUZIX_FINALIZE_SPOOL}/packages" -maxdepth 1 -type f -name "LibreOfficeWriter-*.auzix.tar.gz" -print -quit)"
      wrapper="$(tar -tzf "${archive}" | grep "/Commands/lowriter$" | head -n1)"
      tar -xOzf "${archive}" "${wrapper}" | grep "^runtime_packages=" | grep -F "Libnghttp39" >/dev/null
      (cd "${AUZIX_CANDIDATE_RELEASE}" && sha256sum repo/index.json release-manifest.json >SHA256SUMS && sha256sum -c SHA256SUMS)
      mv "${AUZIX_CANDIDATE_RELEASE}" "${AUZIX_TARGET_RELEASE}"
    ')"
  cat >"${RECEIPT}" <<EOF
format=auzix-writer-finalize-v1
run_id=${RUN_ID}
auzix_tag=${AUZIX_TAG}
source_release_id=${SOURCE_RELEASE_ID}
target_release_id=${TARGET_RELEASE_ID}
package=LibreOfficeWriter
container_name=${CONTAINER_NAME}
container_id=${container_id}
status=running
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
  log "started ${CONTAINER_NAME}; receipt=${RECEIPT}"
}

status() {
  docker ps -a --format '{{.Names}}' | grep -Fx "${CONTAINER_NAME}" >/dev/null || fail "container not found"
  state="$(docker inspect -f '{{.State.Status}}' "${CONTAINER_NAME}")"
  exit_code="$(docker inspect -f '{{.State.ExitCode}}' "${CONTAINER_NAME}")"
  log "container=${CONTAINER_NAME} state=${state} exit_code=${exit_code}"
  docker logs --tail 200 "${CONTAINER_NAME}" || true
  if [[ "${state}" == exited ]]; then
    if [[ "${exit_code}" == 0 && -s "${TARGET_RELEASE}/SHA256SUMS" ]]; then
      printf 'status=complete\ncompleted_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"${RECEIPT}"
    else
      printf 'status=failed\nexit_code=%s\nfailed_at=%s\n' "${exit_code}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"${RECEIPT}"
      fail "Writer finalization failed"
    fi
  fi
}

case "${MODE}" in preflight) preflight ;; start) start ;; status) status ;; *) fail "unknown mode: ${MODE}" ;; esac

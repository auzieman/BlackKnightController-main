#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-preflight}"
WORK_ROOT="${AUZIX_WORK_ROOT:-/var/lib/auzix-build/native-rebase-runs}"
BASE_RUN_ID="${AUZIX_BASE_RUN_ID:-trixie-base-20260824-r3}"
LEGACY_RUN_ID="${AUZIX_LEGACY_RUN_ID:-52d2243d-89d8-4f08-a85f-4152850655ed}"
RELEASE_ID="${AUZIX_RELEASE_ID:-trixie-consolidated-20260826-r1}"
EXPECTED_REPACK_COUNT="${AUZIX_EXPECTED_REPACK_COUNT:-1133}"
SAFE_COUNT_MIN="${AUZIX_SAFE_COUNT_MIN:-300}"
SAFE_COUNT_MAX="${AUZIX_SAFE_COUNT_MAX:-350}"
BASE_SRC="${WORK_ROOT}/${BASE_RUN_ID}/src"
SPOOL="${BASE_SRC}/artifacts/auzix/package-spool-${BASE_RUN_ID}"
LEGACY_REPO="${WORK_ROOT}/${LEGACY_RUN_ID}/src/artifacts/auzix/repo"
RELEASE_ROOT="${AUZIX_RELEASE_ROOT:-/var/lib/auzix-build/releases/${RELEASE_ID}}"
REPO="${RELEASE_ROOT}/repo"
RECEIPT="${AUZIX_RECEIPT_DIR:-/var/lib/auzix-build/receipts}/release-repository-${RELEASE_ID}.receipt"

log() { printf '[auzix-release-repo] %s\n' "$*"; }
fail() { log "FAIL: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }

preflight() {
  need python3; need jq; need sha256sum; need tar
  [[ -s "${LEGACY_REPO}/index.json" ]] || fail "legacy index missing: ${LEGACY_REPO}/index.json"
  [[ -d "${LEGACY_REPO}/packages" ]] || fail "legacy package directory missing"
  [[ -d "${SPOOL}/entries" && -d "${SPOOL}/packages" ]] || fail "immutable spool missing: ${SPOOL}"
  local count
  count="$(find "${SPOOL}/entries" -maxdepth 1 -type f -name '*.json' | wc -l | tr -d ' ')"
  [[ "${count}" == "${EXPECTED_REPACK_COUNT}" ]] || fail "repack count ${count}, expected ${EXPECTED_REPACK_COUNT}"
  log "preflight base=${BASE_RUN_ID} repacks=${count} legacy=${LEGACY_RUN_ID} release=${RELEASE_ID}"
}

consolidate() {
  preflight
  [[ ! -e "${RELEASE_ROOT}" ]] || fail "release output already exists: ${RELEASE_ROOT}"
  mkdir -p "${REPO}/packages" "$(dirname "${RECEIPT}")"
  python3 - "${SPOOL}" "${LEGACY_REPO}" "${RELEASE_ROOT}" "${SAFE_COUNT_MIN}" "${SAFE_COUNT_MAX}" <<'PY'
import hashlib, json, os, shutil, sys
from pathlib import Path

spool, legacy, release = map(Path, sys.argv[1:4])
safe_min, safe_max = map(int, sys.argv[4:6])
repo = release / "repo"

def load_entries(directory):
    result = {}
    for path in sorted(directory.glob("*.json")):
        item = json.loads(path.read_text())
        name = str(item.get("name") or "").strip()
        if not name:
            raise SystemExit(f"entry without name: {path}")
        key = name.casefold()
        if key in result:
            raise SystemExit(f"duplicate package identity: {name}")
        result[key] = (item, path)
    return result

incoming = load_entries(spool / "entries")
legacy_index = json.loads((legacy / "index.json").read_text())
legacy_by_name = {}
for item in legacy_index.get("packages", []):
    name = str(item.get("name") or "").strip()
    if name:
        legacy_by_name[name.casefold()] = item

required = {
    str(dep).casefold()
    for item, _ in incoming.values()
    for dep in (item.get("depends") or [])
    if str(dep).strip()
}
safe_keys = sorted((required - set(incoming)) & set(legacy_by_name))
if not safe_min <= len(safe_keys) <= safe_max:
    raise SystemExit(f"safe reuse count {len(safe_keys)} outside {safe_min}..{safe_max}")

def archive_for(item, package_dir):
    rel = Path(str(item.get("package") or ""))
    path = package_dir / rel.name
    if not path.is_file():
        raise SystemExit(f"archive missing for {item.get('name')}: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = str(item.get("sha256") or "")
    if expected and digest != expected:
        raise SystemExit(f"hash mismatch for {item.get('name')}")
    return path, digest

selected = []
for key in safe_keys:
    item = legacy_by_name[key]
    suite = str((item.get("source") or {}).get("suite") or "").casefold()
    if suite and "trixie" not in suite:
        raise SystemExit(f"non-Trixie safe candidate: {item.get('name')} suite={suite}")
    text = json.dumps(item, sort_keys=True)
    if "/Programs/Libc6/current" in text:
        raise SystemExit(f"alternate glibc candidate: {item.get('name')}")
    source, digest = archive_for(item, legacy / "packages")
    target = repo / "packages" / source.name
    os.link(source, target) if source.stat().st_dev == target.parent.stat().st_dev else shutil.copy2(source, target)
    selected.append(dict(item, sha256=digest))

for item, entry_path in incoming.values():
    source, digest = archive_for(item, spool / "packages")
    target = repo / "packages" / source.name
    os.link(source, target) if source.stat().st_dev == target.parent.stat().st_dev else shutil.copy2(source, target)
    metadata = source.with_name(source.name.removesuffix(".auzix.tar.gz") + ".metadata.tsv")
    if metadata.is_file():
        mt = repo / "packages" / metadata.name
        os.link(metadata, mt) if metadata.stat().st_dev == mt.parent.stat().st_dev else shutil.copy2(metadata, mt)
    selected.append(dict(item, sha256=digest))

by_name = {}
for item in selected:
    key = str(item["name"]).casefold()
    if key in by_name:
        raise SystemExit(f"duplicate merged identity: {item['name']}")
    by_name[key] = item
packages = [by_name[k] for k in sorted(by_name)]
index = {"format":"auzix-repo-v1", "release_id":release.name, "packages":packages}
(repo / "index.json").write_text(json.dumps(index, indent=2) + "\n")
(release / "safe-reuse.packages").write_text("".join(legacy_by_name[k]["name"] + "\n" for k in safe_keys))
manifest = {
    "format":"auzix-consolidated-release-v1", "release_id":release.name,
    "repacked_count":len(incoming), "safe_reuse_count":len(safe_keys),
    "package_count":len(packages), "repository_index":"repo/index.json"
}
(release / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
PY
  sha256sum "${REPO}/index.json" "${RELEASE_ROOT}/release-manifest.json" >"${RELEASE_ROOT}/SHA256SUMS"
  printf 'format=auzix-release-repository-v1\nrelease_id=%s\nstatus=complete\nmanifest=%s\ncompleted_at=%s\n' \
    "${RELEASE_ID}" "${RELEASE_ROOT}/release-manifest.json" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${RECEIPT}"
  verify
}

verify() {
  [[ -s "${RELEASE_ROOT}/release-manifest.json" && -s "${REPO}/index.json" ]] || fail "release manifest/index missing"
  (cd "${RELEASE_ROOT}" && sha256sum -c SHA256SUMS)
  jq -e --argjson low "${SAFE_COUNT_MIN}" --argjson high "${SAFE_COUNT_MAX}" \
    '.repacked_count == 1133 and (.safe_reuse_count >= $low and .safe_reuse_count <= $high) and .package_count == (.repacked_count + .safe_reuse_count)' \
    "${RELEASE_ROOT}/release-manifest.json" >/dev/null || fail "manifest count gate failed"
  local indexed archives
  indexed="$(jq '.packages | length' "${REPO}/index.json")"
  archives="$(find "${REPO}/packages" -maxdepth 1 -type f -name '*.auzix.tar.gz' | wc -l | tr -d ' ')"
  [[ "${indexed}" == "${archives}" ]] || fail "index/archive mismatch: ${indexed}/${archives}"
  log "verified release=${RELEASE_ID} packages=${indexed} manifest=${RELEASE_ROOT}/release-manifest.json"
}

case "${MODE}" in
  preflight) preflight ;;
  consolidate) consolidate ;;
  verify) verify ;;
  *) fail "unknown mode: ${MODE}" ;;
esac

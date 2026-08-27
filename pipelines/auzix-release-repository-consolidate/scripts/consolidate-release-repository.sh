#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-preflight}"
WORK_ROOT="${AUZIX_WORK_ROOT:-/var/lib/auzix-build/native-rebase-runs}"
BASE_RUN_ID="${AUZIX_BASE_RUN_ID:-trixie-base-20260824-r3}"
LEGACY_RUN_ID="${AUZIX_LEGACY_RUN_ID:-52d2243d-89d8-4f08-a85f-4152850655ed}"
RELEASE_ID="${AUZIX_RELEASE_ID:-trixie-consolidated-20260826-r3}"
RELEASE_ROOTS="${AUZIX_RELEASE_ROOTS:-Glances}"
# Runtime proof packages must come from a current, explicitly reviewed spool.
# Glances was repaired in the preserved proof root after the old repository
# archive was cut, so it must be re-archived as a supplement, never safe-reused.
REQUIRED_FRESH_PACKAGES="${AUZIX_REQUIRED_FRESH_PACKAGES:-Glances}"
EXPECTED_REPACK_COUNT="${AUZIX_EXPECTED_REPACK_COUNT:-1133}"
SAFE_COUNT_MIN="${AUZIX_SAFE_COUNT_MIN:-487}"
SAFE_COUNT_MAX="${AUZIX_SAFE_COUNT_MAX:-487}"
BASE_SRC="${WORK_ROOT}/${BASE_RUN_ID}/src"
SPOOL="${BASE_SRC}/artifacts/auzix/package-spool-${BASE_RUN_ID}"
LEGACY_REPO="${WORK_ROOT}/${LEGACY_RUN_ID}/src/artifacts/auzix/repo"
LEGACY_ROOT="${WORK_ROOT}/${LEGACY_RUN_ID}/src/out/auzix-strict/AuzixRoot"
SUPPLEMENT_SPOOL="${BASE_SRC}/artifacts/auzix/package-spool-release-supplement-${RELEASE_ID}"
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
  local count safe_count
  count="$(find "${SPOOL}/entries" -maxdepth 1 -type f -name '*.json' | wc -l | tr -d ' ')"
  [[ "${count}" == "${EXPECTED_REPACK_COUNT}" ]] || fail "repack count ${count}, expected ${EXPECTED_REPACK_COUNT}"
  safe_count="$(python3 - "${SPOOL}/entries" "${LEGACY_REPO}/index.json" "${RELEASE_ROOTS}" "${REQUIRED_FRESH_PACKAGES}" <<'PY'
import json, sys
from pathlib import Path
entries, legacy_index = Path(sys.argv[1]), Path(sys.argv[2])
release_roots = sys.argv[3].split()
required_fresh = {name.casefold() for name in sys.argv[4].split()}
incoming = [json.loads(p.read_text()) for p in entries.glob("*.json")]
incoming_names = {str(x.get("name") or "").casefold() for x in incoming}
legacy = {str(x.get("name") or "").casefold(): x for x in json.loads(legacy_index.read_text()).get("packages", [])}
selected=set(incoming_names); queue=list(incoming)
for name in release_roots:
    key=name.casefold()
    if key not in legacy: raise SystemExit(f"release root absent from legacy repository: {name}")
    if key not in selected:
        # Walk dependencies from the preserved metadata, but do not count a
        # required-fresh root itself as reusable.
        if key not in required_fresh: selected.add(key)
        queue.append(legacy[key])
while queue:
    item=queue.pop()
    for dep in item.get("depends") or []:
        key=str(dep).casefold()
        if key in selected or key not in legacy: continue
        selected.add(key); queue.append(legacy[key])
print(len(selected - incoming_names))
PY
)"
  (( safe_count >= SAFE_COUNT_MIN && safe_count <= SAFE_COUNT_MAX )) ||
    fail "safe reuse count ${safe_count} outside ${SAFE_COUNT_MIN}..${SAFE_COUNT_MAX}"
  log "preflight base=${BASE_RUN_ID} repacks=${count} safe_reuse=${safe_count} legacy=${LEGACY_RUN_ID} release=${RELEASE_ID}"
}

supplement() {
  need jq; need tar; need sha256sum
  rm -rf "${SUPPLEMENT_SPOOL}"
  local package receipt
  for package in LibreOfficeCoreNogui Glances; do
    receipt="$(find "${LEGACY_ROOT}/System/PackageDB" -maxdepth 1 -type f -name "${package}-*.auzix.json" -print -quit)"
    [[ -n "${receipt}" ]] || fail "${package} receipt missing"
    AUZIX_PACKAGE_NORMALIZE_OWNERS=0 AUZIX_REPO_REJECT_ALT_GLIBC=1 \
      "${BASE_SRC}/scripts/package-auzix-receipt-archive.sh" "${LEGACY_ROOT}" "${receipt}" "${SUPPLEMENT_SPOOL}"
  done
  [[ "$(find "${SUPPLEMENT_SPOOL}/entries" -maxdepth 1 -type f -name '*.json' | wc -l | tr -d ' ')" == 2 ]] ||
    fail "supplement spool did not produce exactly two entries"
  log "supplemented LibreOfficeCoreNogui and corrected Glances from preserved receipts and payloads"
}

consolidate() {
  preflight
  [[ ! -e "${RELEASE_ROOT}" ]] || fail "release output already exists: ${RELEASE_ROOT}"
  mkdir -p "${REPO}/packages" "$(dirname "${RECEIPT}")"
  [[ -d "${SUPPLEMENT_SPOOL}/entries" && -d "${SUPPLEMENT_SPOOL}/packages" ]] || fail "supplement spool missing"
  python3 - "${SPOOL}" "${SUPPLEMENT_SPOOL}" "${LEGACY_REPO}" "${RELEASE_ROOT}" "${SAFE_COUNT_MIN}" "${SAFE_COUNT_MAX}" "${RELEASE_ROOTS}" "${REQUIRED_FRESH_PACKAGES}" <<'PY'
import hashlib, json, os, shutil, sys
from pathlib import Path

spool, supplement, legacy, release = map(Path, sys.argv[1:5])
safe_min, safe_max = map(int, sys.argv[5:7])
release_roots = sys.argv[7].split()
required_fresh = sys.argv[8].split()
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
supplements = load_entries(supplement / "entries")
legacy_index = json.loads((legacy / "index.json").read_text())
legacy_by_name = {}
for item in legacy_index.get("packages", []):
    name = str(item.get("name") or "").strip()
    if name:
        legacy_by_name[name.casefold()] = item

provided = dict(incoming)
provided.update(supplements)
missing_fresh = [name for name in required_fresh if name.casefold() not in provided]
if missing_fresh:
    raise SystemExit(
        "required fresh runtime packages would fall back to legacy safe reuse: "
        + " ".join(missing_fresh)
    )
selected_keys = set(provided)
queue = [item for item, _ in provided.values()]
for name in release_roots:
    key = name.casefold()
    if key not in legacy_by_name:
        raise SystemExit(f"release root absent from legacy repository: {name}")
    if key not in selected_keys:
        selected_keys.add(key); queue.append(legacy_by_name[key])
while queue:
    item = queue.pop()
    for dep in item.get("depends") or []:
        key = str(dep).casefold()
        if key in selected_keys or key not in legacy_by_name:
            continue
        selected_keys.add(key)
        queue.append(legacy_by_name[key])
safe_keys = sorted(selected_keys - set(provided))
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

for item, entry_path in supplements.values():
    source, digest = archive_for(item, supplement / "packages")
    target = repo / "packages" / source.name
    os.link(source, target) if source.stat().st_dev == target.parent.stat().st_dev else shutil.copy2(source, target)
    selected.append(dict(item, sha256=digest))

by_name = {}
for item in selected:
    key = str(item["name"]).casefold()
    if key in by_name:
        raise SystemExit(f"duplicate merged identity: {item['name']}")
    by_name[key] = item
packages = [by_name[k] for k in sorted(by_name)]
unresolved_catalog_edges = sorted({
    str(dep) for item in packages for dep in (item.get("depends") or [])
    if str(dep).strip() and str(dep).casefold() not in by_name
})
index = {"format":"auzix-repo-v1", "release_id":release.name, "packages":packages}
(repo / "index.json").write_text(json.dumps(index, indent=2) + "\n")
(release / "safe-reuse.packages").write_text("".join(legacy_by_name[k]["name"] + "\n" for k in safe_keys))
manifest = {
    "format":"auzix-consolidated-release-v1", "release_id":release.name,
    "repacked_count":len(incoming), "safe_reuse_count":len(safe_keys),
    "supplement_count":len(supplements),
    "release_roots":release_roots,
    "package_count":len(packages),
    "unresolved_catalog_edge_count":len(unresolved_catalog_edges),
    "closure_rule":"Enforce closure against an explicit install/profile selection, not the complete repository catalog.",
    "repository_index":"repo/index.json"
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
    '.repacked_count == 1133 and .supplement_count == 2 and (.safe_reuse_count >= $low and .safe_reuse_count <= $high) and .package_count == (.repacked_count + .safe_reuse_count + .supplement_count)' \
    "${RELEASE_ROOT}/release-manifest.json" >/dev/null || fail "manifest count gate failed"
  local indexed archives
  indexed="$(jq '.packages | length' "${REPO}/index.json")"
  archives="$(find "${REPO}/packages" -maxdepth 1 -type f -name '*.auzix.tar.gz' | wc -l | tr -d ' ')"
  [[ "${indexed}" == "${archives}" ]] || fail "index/archive mismatch: ${indexed}/${archives}"
  log "verified release=${RELEASE_ID} packages=${indexed} manifest=${RELEASE_ROOT}/release-manifest.json"
}

case "${MODE}" in
  preflight) preflight ;;
  supplement) supplement ;;
  consolidate) consolidate ;;
  verify) verify ;;
  *) fail "unknown mode: ${MODE}" ;;
esac

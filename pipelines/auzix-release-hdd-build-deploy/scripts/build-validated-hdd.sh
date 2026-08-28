#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-preflight}"
HDD_ID="${AUZIX_HDD_ID:-desktop-main-20260828-r7}"
SOURCE_REF="${AUZIX_SOURCE_REF:-auzix-alpha-package-profile-hdd-20260828-r4}"
BUILD_ROOT="${AUZIX_BUILD_ROOT:-/var/lib/auzix-build}"
WORK="${BUILD_ROOT}/hdd-runs/${HDD_ID}"
OUT="${BUILD_ROOT}/hdd-images/${HDD_ID}"
IMAGE="${OUT}/auzix-${HDD_ID}.img"
RECEIPT="${BUILD_ROOT}/receipts/release-hdd-${HDD_ID}.receipt"
PROFILE_SOURCE="${AUZIX_PROFILE_SOURCE:-/srv/nfs/swarm/blackknightcontroller/runtime/pipelines/auzix-release-hdd-build-deploy/profiles/desktop-main.packages}"
R5_REPO="${AUZIX_R5_REPO:-${BUILD_ROOT}/releases/trixie-consolidated-20260828-r5/repo}"
COMPOSITE_REPO="${AUZIX_COMPOSITE_REPO:-${BUILD_ROOT}/native-rebase-runs/trixie-base-20260824-r3/src/artifacts/auzix/repo-trixie-composite-proof}"
NATIVE_REPO="${AUZIX_NATIVE_REPO:-${BUILD_ROOT}/runs/strict-r8-busybox-xorg-dns-20260823T174356Z/src/artifacts/auzix/repo}"
WRITER_SPOOL="${AUZIX_WRITER_SPOOL:-${BUILD_ROOT}/package-finalizations/libreoffice-writer-finalize-20260828-r2/spool}"
SEED_ROOT="${AUZIX_SEED_ROOT:-${BUILD_ROOT}/runs/strict-r8-busybox-xorg-dns-20260823T174356Z/src/out/auzix-strict/AuzixRoot}"
BOOT_SOURCE="${AUZIX_BOOT_SOURCE:-${BUILD_ROOT}/runs/strict-tty-spice-r4-20260823T005327Z/src/out/auzix-iso/iso/boot}"
BARE_REPO="${AUZIX_BARE_REPO:-/srv/auzix/git-remotes/AuziX.git}"
IMAGE_SIZE="${AUZIX_IMAGE_SIZE:-8G}"
MIN_ARCHIVE_BYTES="${AUZIX_MIN_ARCHIVE_BYTES:-536870912}"
MAX_ARCHIVE_BYTES="${AUZIX_MAX_ARCHIVE_BYTES:-2684354560}"
REPO_PORT="${AUZIX_REPO_PORT:-18089}"

log() { printf '[auzix-profile-hdd] %s\n' "$*"; }
fail() { log "FAIL: $*" >&2; exit 1; }

check_inputs() {
  for command in python3 jq sha256sum tar truncate losetup parted partprobe partx mkfs.ext4 mount umount chroot git; do
    command -v "${command}" >/dev/null || fail "missing command: ${command}"
  done
  [[ "$(hostname -s)" == lab-ai-worker || "$(hostname -s)" == r730-ai-01 ]] || fail "must run on lab-build"
  [[ -s "${PROFILE_SOURCE}" ]] || fail "profile missing: ${PROFILE_SOURCE}"
  [[ -s "${R5_REPO}/index.json" && -s "${COMPOSITE_REPO}/index.json" && -s "${NATIVE_REPO}/index.json" ]] || fail "catalog input missing"
  [[ -d "${WRITER_SPOOL}/entries" && -d "${WRITER_SPOOL}/packages" ]] || fail "Writer spool missing"
  [[ -x "${SEED_ROOT}/Programs/BusyBox/1.36.1/Commands/busybox" ]] || fail "bootstrap seed lacks BusyBox"
  [[ -x "${SEED_ROOT}/System/Tools/auzix-install-root-from-repo-profile" ]] || fail "bootstrap seed lacks installer"
  [[ -s "${BOOT_SOURCE}/vmlinuz" && -s "${BOOT_SOURCE}/initramfs.cpio.gz" ]] || fail "boot seed missing"
  git --git-dir="${BARE_REPO}" rev-parse "${SOURCE_REF}^{commit}" >/dev/null || fail "immutable AUZiX source ref missing"
  [[ ! -e "${OUT}" && ! -e "${WORK}" ]] || fail "run output already exists for ${HDD_ID}"
}

resolve_catalog() {
  local destination="$1"
  python3 - "${R5_REPO}" "${COMPOSITE_REPO}" "${NATIVE_REPO}" "${WRITER_SPOOL}" "${PROFILE_SOURCE}" "${destination}" "${MIN_ARCHIVE_BYTES}" "${MAX_ARCHIVE_BYTES}" <<'PY'
import hashlib, json, os, shutil, sys
from pathlib import Path
r5, composite, native, writer, profile, destination = map(Path, sys.argv[1:7])
low, high = map(int, sys.argv[7:9])
catalog = {}
for repo, label in [(native, "native"), (composite, "composite"), (r5, "r5")]:
    data = json.loads((repo / "index.json").read_text())
    for item in data.get("packages", data if isinstance(data, list) else []):
        catalog[str(item["name"]).casefold()] = (item, repo, label)
for entry in (writer / "entries").glob("*.json"):
    item = json.loads(entry.read_text())
    catalog[str(item["name"]).casefold()] = (item, writer, "writer")
roots = []
for line in profile.read_text().splitlines():
    line = line.split("#", 1)[0].strip()
    if line and not (line.startswith("[") and line.endswith("]")): roots.append(line)
seen, active, order, missing = set(), set(), [], []
external = {"libc6", "libgccs1", "gcc14base"}
def visit(name, parent="profile"):
    key = str(name).casefold()
    if key in seen or key in external: return
    if key in active: return
    record = catalog.get(key)
    if record is None:
        missing.append((str(name), parent)); return
    active.add(key); item, _, _ = record
    for dependency in item.get("depends") or []: visit(dependency, item["name"])
    active.remove(key); seen.add(key); order.append(record)
for root in roots: visit(root)
if missing: raise SystemExit("missing dependency edges: " + ", ".join(f"{n}<-{p}" for n,p in missing[:60]))
archive_bytes = 0; source_counts = {}; selected = []
if str(destination) != "-":
    packages_dir = destination / "packages"; packages_dir.mkdir(parents=True)
for item, repo, label in order:
    archive = repo / "packages" / Path(item["package"]).name
    if not archive.is_file(): raise SystemExit(f"archive missing: {archive}")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if item.get("sha256") and item["sha256"] != digest: raise SystemExit(f"hash mismatch: {item['name']}")
    archive_bytes += archive.stat().st_size; source_counts[label] = source_counts.get(label, 0) + 1
    selected.append(dict(item, sha256=digest))
    if str(destination) != "-":
        target = packages_dir / archive.name
        os.link(archive, target) if archive.stat().st_dev == packages_dir.stat().st_dev else shutil.copy2(archive, target)
if not low <= archive_bytes <= high: raise SystemExit(f"selected archive bytes {archive_bytes} outside {low}..{high}")
receipt = {"format":"auzix-hdd-selection-v1","roots":roots,"root_count":len(roots),"package_count":len(selected),"archive_bytes":archive_bytes,"sources":source_counts,"packages":selected}
print(f"profile roots={len(roots)} closure={len(selected)} archive_bytes={archive_bytes} sources={source_counts}")
if str(destination) != "-":
    install_index = [{key: item[key] for key in ("name", "version", "package", "sha256", "depends", "hooks") if key in item} for item in selected]
    (destination / "index.json").write_text(json.dumps({"format":"auzix-repo-v1","release_id":destination.parent.name,"packages":install_index}, separators=(",", ":"))+"\n")
    (destination.parent / "selection-receipt.json").write_text(json.dumps(receipt, indent=2)+"\n")
PY
}

preflight() {
  check_inputs
  resolve_catalog -
  log "PASS hdd=${HDD_ID} image_size=${IMAGE_SIZE} package_only=1"
}

cleanup_mounts() {
  set +e
  [[ -n "${SERVER_PID:-}" ]] && kill "${SERVER_PID}" 2>/dev/null
  for path in workspace boot run/auzix-hdd sys proc dev; do mountpoint -q "${SEED_ROOT}/${path}" && umount -R "${SEED_ROOT}/${path}"; done
  [[ -n "${LOOP_DEV:-}" ]] && losetup -d "${LOOP_DEV}" 2>/dev/null
}

build() {
  preflight
  mkdir -p "${WORK}/repo" "${WORK}/source" "${WORK}/boot" "${OUT}" "$(dirname "${RECEIPT}")"
  resolve_catalog "${WORK}/repo"
  cp "${PROFILE_SOURCE}" "${WORK}/desktop-main.packages"
  cp -p "${BOOT_SOURCE}/vmlinuz" "${BOOT_SOURCE}/initramfs.cpio.gz" "${WORK}/boot/"
  mkdir -p "${WORK}/host-jq-libs"
  cp -L /usr/bin/jq "${WORK}/host-jq.real"
  cp -L /lib64/ld-linux-x86-64.so.2 "${WORK}/host-jq-loader"
  ldd /usr/bin/jq | awk '{for (i=1;i<=NF;i++) if ($i ~ /^\//) print $i}' | sort -u |
    xargs -r cp -L -t "${WORK}/host-jq-libs"
  cat >"${WORK}/host-jq" <<'EOF'
#!/Programs/BusyBox/1.36.1/Commands/busybox sh
exec /run/auzix-hdd/host-jq-loader \
  --library-path /run/auzix-hdd/host-jq-libs \
  /run/auzix-hdd/host-jq.real "$@"
EOF
  chmod 0755 "${WORK}/host-jq"
  commit="$(git --git-dir="${BARE_REPO}" rev-parse "${SOURCE_REF}^{commit}")"
  git --git-dir="${BARE_REPO}" archive "${commit}" | tar -x -C "${WORK}/source"
  truncate -s "${IMAGE_SIZE}" "${IMAGE}"
  trap cleanup_mounts EXIT
  LOOP_DEV="$(losetup --find --show --partscan "${IMAGE}")"
  parted -s "${LOOP_DEV}" mklabel msdos
  parted -s "${LOOP_DEV}" mkpart primary ext4 1MiB 60%
  parted -s "${LOOP_DEV}" mkpart primary ext4 60% 80%
  parted -s "${LOOP_DEV}" mkpart primary ext4 80% 100%
  parted -s "${LOOP_DEV}" set 1 boot on
  partprobe "${LOOP_DEV}" 2>/dev/null || true
  partx -u "${LOOP_DEV}" 2>/dev/null || partx -a "${LOOP_DEV}" 2>/dev/null || true
  for _ in $(seq 1 50); do [[ -b "${LOOP_DEV}p3" ]] && break; sleep 0.1; done
  [[ -b "${LOOP_DEV}p1" && -b "${LOOP_DEV}p2" && -b "${LOOP_DEV}p3" ]] || fail "sparse image partition nodes did not appear"
  mkfs.ext4 -q -F -L AUZIXROOT "${LOOP_DEV}p1"
  mkfs.ext4 -q -F -L AUZIXHOME "${LOOP_DEV}p2"
  mkfs.ext4 -q -F -L AUZIXWORK "${LOOP_DEV}p3"
  mkdir -p "${SEED_ROOT}/workspace" "${SEED_ROOT}/boot" "${SEED_ROOT}/run/auzix-hdd"
  mount --bind "${WORK}/source" "${SEED_ROOT}/workspace"
  mount --bind "${WORK}/boot" "${SEED_ROOT}/boot"
  mount --bind "${WORK}" "${SEED_ROOT}/run/auzix-hdd"
  mount --rbind /dev "${SEED_ROOT}/dev"; mount --make-rslave "${SEED_ROOT}/dev"
  mount -t proc proc "${SEED_ROOT}/proc"
  mount --rbind /sys "${SEED_ROOT}/sys"; mount --make-rslave "${SEED_ROOT}/sys"
  python3 -m http.server "${REPO_PORT}" --bind 127.0.0.1 --directory "${WORK}/repo" >"${WORK}/repo-server.log" 2>&1 & SERVER_PID=$!
  python3 - "${REPO_PORT}" <<'PY'
import socket, sys, time
port = int(sys.argv[1])
for _ in range(100):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            raise SystemExit(0)
    except OSError:
        time.sleep(0.1)
raise SystemExit(f"repository server did not become ready on 127.0.0.1:{port}")
PY
  chroot "${SEED_ROOT}" /Programs/BusyBox/1.36.1/Commands/busybox env \
    AUZIX_INSTALL_COPY_SEED_RUNTIME=0 AUZIX_INSTALL_PREPARED_TARGET=1 \
    AUZIX_INSTALL_JQ=/run/auzix-hdd/host-jq AUZIX_LINK_MODE=strict \
    /Programs/BusyBox/1.36.1/Commands/busybox sh /workspace/scripts/auzix-install-root-from-repo-profile.sh --force \
    --repo "http://127.0.0.1:${REPO_PORT}" --profile /run/auzix-hdd/desktop-main.packages "${LOOP_DEV}" \
    2>&1 | tee "${WORK}/installer.log"
  cleanup_mounts; trap - EXIT
  sha256sum "${IMAGE}" >"${IMAGE}.sha256"
  allocated="$(du -B1 "${IMAGE}" | awk '{print $1}')"
  selected="$(jq -r .package_count "${WORK}/selection-receipt.json")"
  archive_bytes="$(jq -r .archive_bytes "${WORK}/selection-receipt.json")"
  printf 'format=auzix-package-profile-hdd-v1\nhdd_id=%s\nsource_ref=%s\nsource_commit=%s\nprofile=%s\nselected_packages=%s\narchive_bytes=%s\nimage=%s\nimage_allocated_bytes=%s\nstatus=built\ncompleted_at=%s\n' \
    "${HDD_ID}" "${SOURCE_REF}" "${commit}" "${PROFILE_SOURCE}" "${selected}" "${archive_bytes}" "${IMAGE}" "${allocated}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${RECEIPT}"
  log "PASS image=${IMAGE} packages=${selected} allocated=${allocated}"
}

case "${MODE}" in preflight) preflight ;; build) build ;; *) fail "unknown mode: ${MODE}" ;; esac

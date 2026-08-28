#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-preflight}"
HDD_ID="${AUZIX_HDD_ID:-desktop-main-20260828-r8}"
SOURCE_REF="${AUZIX_SOURCE_REF:-auzix-alpha-package-profile-hdd-20260828-r4}"
BUILD_ROOT="${AUZIX_BUILD_ROOT:-/var/lib/auzix-build}"
PIPELINE_ROOT="${AUZIX_PIPELINE_ROOT:-/srv/nfs/swarm/blackknightcontroller/runtime/pipelines/auzix-release-hdd-build-deploy}"
WORK="${BUILD_ROOT}/hdd-runs/${HDD_ID}"
OUT="${BUILD_ROOT}/hdd-images/${HDD_ID}"
IMAGE="${OUT}/auzix-${HDD_ID}.img"
RECEIPT="${BUILD_ROOT}/receipts/release-hdd-${HDD_ID}.receipt"
STATUS="${BUILD_ROOT}/receipts/hdd-${HDD_ID}.status"
PROFILE="${PIPELINE_ROOT}/profiles/desktop-main.packages"
PLAN_TOOL="${PIPELINE_ROOT}/scripts/resolve-hdd-install-plan.py"
UNPACK_TOOL="${PIPELINE_ROOT}/scripts/prove-hdd-bootstrap-root.sh"
CONFIGURE_TOOL="${PIPELINE_ROOT}/scripts/configure-hdd-proof-root.sh"
BARE_REPO="/srv/auzix/git-remotes/AuziX.git"
BOOT_SOURCE="${BUILD_ROOT}/runs/strict-tty-spice-r4-20260823T005327Z/src/out/auzix-iso/iso/boot"
RUNTIME_SOURCE="${BUILD_ROOT}/runs/strict-r8-busybox-xorg-dns-20260823T174356Z/src/out/auzix-strict/AuzixRoot/System/Libraries/Runtime/glibc"
PLAN_HASH="31a6dbf8a88976fbff0c16787d413e26aff15e6c6b1c0488e78e509f818e7bf6"
LOOP_DEV="" TARGET="" STAGE=preflight

log() { printf '[auzix-hdd] %s\n' "$*"; }
status() { mkdir -p "$(dirname "${STATUS}")"; printf 'hdd_id=%s\nstate=%s\nstage=%s\nupdated_at=%s\n' "${HDD_ID}" "$1" "$2" "$(date -u +%FT%TZ)" >"${STATUS}"; }
cleanup() {
  rc=$?; set +e
  for path in "${TARGET}/Work" "${TARGET}/Home" "${TARGET}"; do [[ -n "${TARGET}" ]] && mountpoint -q "${path}" && umount "${path}"; done
  [[ -n "${LOOP_DEV}" ]] && losetup -d "${LOOP_DEV}" 2>/dev/null
  if (( rc != 0 )); then status failed "${STAGE}:exit=${rc}"; fi
  return "${rc}"
}

preflight() {
  for cmd in python3 jq tar sha256sum truncate losetup parted partprobe partx mkfs.ext4 mount umount grub-install git; do command -v "${cmd}" >/dev/null || { log "FAIL missing ${cmd}"; exit 1; }; done
  [[ -x "${PLAN_TOOL}" && -x "${UNPACK_TOOL}" && -x "${CONFIGURE_TOOL}" ]] || { log 'FAIL transaction tools missing'; exit 1; }
  [[ -s "${BOOT_SOURCE}/vmlinuz" && -s "${BOOT_SOURCE}/initramfs.cpio.gz" ]] || { log 'FAIL boot payload missing'; exit 1; }
  git --git-dir="${BARE_REPO}" rev-parse "${SOURCE_REF}^{commit}" >/dev/null || { log 'FAIL immutable source missing'; exit 1; }
  [[ ! -e "${WORK}" && ! -e "${OUT}" ]] || { log "FAIL run ID already exists: ${HDD_ID}"; exit 1; }
  plan="$(mktemp)"
  "${PLAN_TOOL}" --r5 "${BUILD_ROOT}/releases/trixie-consolidated-20260828-r5/repo" \
    --composite "${BUILD_ROOT}/native-rebase-runs/trixie-base-20260824-r3/src/artifacts/auzix/repo-trixie-composite-proof" \
    --native "${BUILD_ROOT}/runs/strict-r8-busybox-xorg-dns-20260823T174356Z/src/artifacts/auzix/repo" \
    --writer "${BUILD_ROOT}/package-finalizations/libreoffice-writer-finalize-20260828-r2/spool" \
    --profile "${PROFILE}" --output "${plan}"
  [[ "$(jq -r .package_count "${plan}")" == 642 && "$(jq -r .plan_content_sha256 "${plan}")" == "${PLAN_HASH}" ]] || { log 'FAIL install plan drift'; exit 1; }
  rm -f "${plan}"
  log "PASS preflight packages=642 plan=${PLAN_HASH}"
}

build() {
  preflight
  mkdir -p "${WORK}/source" "${WORK}/transaction" "${OUT}"
  status running source
  commit="$(git --git-dir="${BARE_REPO}" rev-parse "${SOURCE_REF}^{commit}")"
  git --git-dir="${BARE_REPO}" archive "${commit}" | tar -x -C "${WORK}/source"
  truncate -s 8G "${IMAGE}"
  TARGET="${WORK}/target"; mkdir -p "${TARGET}"; trap cleanup EXIT
  STAGE=partition; status running "${STAGE}"
  LOOP_DEV="$(losetup --find --show --partscan "${IMAGE}")"
  parted -s "${LOOP_DEV}" mklabel msdos
  parted -s "${LOOP_DEV}" mkpart primary ext4 1MiB 60%
  parted -s "${LOOP_DEV}" mkpart primary ext4 60% 80%
  parted -s "${LOOP_DEV}" mkpart primary ext4 80% 100%
  parted -s "${LOOP_DEV}" set 1 boot on
  partprobe "${LOOP_DEV}" 2>/dev/null || true; partx -u "${LOOP_DEV}" 2>/dev/null || true
  for _ in $(seq 1 50); do [[ -b "${LOOP_DEV}p3" ]] && break; sleep .1; done
  [[ -b "${LOOP_DEV}p1" && -b "${LOOP_DEV}p2" && -b "${LOOP_DEV}p3" ]]
  mkfs.ext4 -q -F -L AUZIXROOT "${LOOP_DEV}p1"; mkfs.ext4 -q -F -L AUZIXHOME "${LOOP_DEV}p2"; mkfs.ext4 -q -F -L AUZIXWORK "${LOOP_DEV}p3"
  mount "${LOOP_DEV}p1" "${TARGET}"; mkdir -p "${TARGET}/Home" "${TARGET}/Work"; mount "${LOOP_DEV}p2" "${TARGET}/Home"; mount "${LOOP_DEV}p3" "${TARGET}/Work"

  STAGE=packages; status running "${STAGE}"
  AUZIX_TRANSACTION_DIR="${WORK}/transaction" AUZIX_TARGET_ROOT="${TARGET}" AUZIX_SOURCE_ROOT="${WORK}/source" "${UNPACK_TOOL}" "${HDD_ID}" 642
  STAGE=configure; status running "${STAGE}"
  AUZIX_TRANSACTION_DIR="${WORK}/transaction" AUZIX_TARGET_ROOT="${TARGET}" AUZIX_SOURCE_ROOT="${WORK}/source" AUZIX_RUNTIME_SOURCE="${RUNTIME_SOURCE}" "${CONFIGURE_TOOL}" "${HDD_ID}"

  STAGE=boot; status running "${STAGE}"
  mkdir -p "${TARGET}/boot/grub" "${TARGET}/System/Settings"
  cp -p "${BOOT_SOURCE}/vmlinuz" "${BOOT_SOURCE}/initramfs.cpio.gz" "${TARGET}/boot/"
  uuid="$(blkid -s UUID -o value "${LOOP_DEV}p1")"
  printf 'LABEL=AUZIXROOT / ext4 defaults 0 1\nLABEL=AUZIXHOME /Home ext4 defaults 0 2\nLABEL=AUZIXWORK /Work ext4 defaults 0 2\n' >"${TARGET}/System/Settings/fstab"
  printf 'set timeout=3\nset default=0\nmenuentry "AUZiX" {\n linux /boot/vmlinuz root=UUID=%s init=/init rw console=tty0 console=ttyS0,115200\n initrd /boot/initramfs.cpio.gz\n}\n' "${uuid}" >"${TARGET}/boot/grub/grub.cfg"
  grub-install --target=i386-pc --boot-directory="${TARGET}/boot" "${LOOP_DEV}"
  sync; cleanup; trap - EXIT
  losetup -a | grep -Fq "${IMAGE}" && { log 'FAIL loop still attached'; status failed cleanup; exit 1; }
  sha256sum "${IMAGE}" >"${IMAGE}.sha256"
  allocated="$(du -B1 "${IMAGE}" | awk '{print $1}')"
  printf 'format=auzix-package-profile-hdd-v2\nhdd_id=%s\nsource_ref=%s\nsource_commit=%s\ninstall_plan_sha256=%s\nselected_packages=642\nimage=%s\nimage_allocated_bytes=%s\nstatus=built\ncompleted_at=%s\n' "${HDD_ID}" "${SOURCE_REF}" "${commit}" "${PLAN_HASH}" "${IMAGE}" "${allocated}" "$(date -u +%FT%TZ)" >"${RECEIPT}"
  status built complete; log "PASS image=${IMAGE} allocated=${allocated}"
}

case "${MODE}" in preflight) preflight ;; build) build ;; *) exit 2 ;; esac

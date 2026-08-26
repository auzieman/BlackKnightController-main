#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-deploy}"
RELEASE_ID="${AUZIX_RELEASE_ID:-trixie-consolidated-20260826-r1}"
HDD_ID="${AUZIX_HDD_ID:-hdd-pve-r1}"
VMID="${AUZIX_TARGET_VMID:-142}"
TARGET_IP="${AUZIX_TARGET_IP:-192.168.1.229}"
STORAGE="${AUZIX_PVE_STORAGE:-local-lvm}"
ALLOW_PROTECTED="${AUZIX_ALLOW_PROTECTED_VMID:-false}"
R730="${AUZIX_R730_HOST:-10.20.0.130}"
PVE="${AUZIX_PVE_HOST:-192.168.1.9}"
KEY="${BKC_SSH_KEY:-/app/keys/bkc_id_rsa}"
KNOWN_HOSTS="${BKC_KNOWN_HOSTS:-/app/runtime/known_hosts}"
IMAGE="/var/lib/auzix-build/releases/${RELEASE_ID}/hdd/${HDD_ID}/auzix-${HDD_ID}.img"
REMOTE_IMAGE="/var/lib/vz/template/iso/auzix-${HDD_ID}.img"
RECEIPT="/var/lib/auzix-build/receipts/release-hdd-${HDD_ID}.receipt"
SSH=(ssh -i "${KEY}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="${KNOWN_HOSTS}")

fail() { printf '[auzix-release-hdd-deploy] FAIL: %s\n' "$*" >&2; exit 1; }
[[ "${VMID}" != 135 || "${ALLOW_PROTECTED}" == true ]] || fail "VMID135 is protected"

deploy() {
  "${SSH[@]}" root@"${R730}" "test -s '${IMAGE}'; grep -Fx status=built '${RECEIPT}'"
  "${SSH[@]}" root@"${R730}" "cat '${IMAGE}'" | "${SSH[@]}" root@"${PVE}" "cat > '${REMOTE_IMAGE}'"
  "${SSH[@]}" root@"${PVE}" bash -s -- "${VMID}" "${STORAGE}" "${REMOTE_IMAGE}" "${HDD_ID}" <<'PVE'
set -euo pipefail
vmid="$1"; storage="$2"; image="$3"; hdd_id="$4"
test "${vmid}" -ne 135
qm config "${vmid}" >/dev/null
qm status "${vmid}" | grep -q running && qm stop "${vmid}" --timeout 30 || true
qm snapshot "${vmid}" "pre-auzix-${hdd_id}" --description "protected checkpoint before AUZiX release HDD" || true
qm set "${vmid}" --delete scsi0 >/dev/null 2>&1 || true
while IFS=: read -r key _; do
  [[ "${key}" =~ ^unused[0-9]+$ ]] && qm set "${vmid}" --delete "${key}" >/dev/null 2>&1 || true
done < <(qm config "${vmid}")
qm importdisk "${vmid}" "${image}" "${storage}"
volume="$(qm config "${vmid}" | awk -F': ' '/^unused[0-9]+:/ {print $2; exit}')"
test -n "${volume}"
qm set "${vmid}" --scsi0 "${volume}" --boot order=scsi0
qm config "${vmid}"
PVE
  "${SSH[@]}" root@"${R730}" "printf 'status=deployed\\ntarget_vmid=${VMID}\\n' >> '${RECEIPT}'"
}

boot_probe() {
  "${SSH[@]}" root@"${PVE}" "qm stop '${VMID}' --skiplock 1 >/dev/null 2>&1 || true; qm start '${VMID}'; qm status '${VMID}'; qm config '${VMID}' | sed -n '1,80p'"
  [[ -n "${TARGET_IP}" ]] || return 0
  for _ in $(seq 1 60); do
    if ping -c 1 -W 1 "${TARGET_IP}" >/dev/null 2>&1; then
      printf '[auzix-release-hdd-deploy] first-boot network reachable: %s\n' "${TARGET_IP}"
      return 0
    fi
    sleep 10
  done
  fail "first-boot network did not become reachable: ${TARGET_IP}"
}

case "${MODE}" in
  deploy) deploy ;;
  boot) boot_probe ;;
  *) fail "unknown mode: ${MODE}" ;;
esac

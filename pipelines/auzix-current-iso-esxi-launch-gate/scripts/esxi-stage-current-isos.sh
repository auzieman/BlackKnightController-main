#!/bin/sh
set -eu

run_id="${AUZIX_RUN_ID:?AUZIX_RUN_ID is required}"
source_host="${AUZIX_BUILD_WORKER:-r730-ai-01}"
source_dir="${AUZIX_BUILD_PUBLISH_DIR:-/var/lib/auzix-build/published}"
esxi_host="${BKC_ESXI_HOST:-lab-esxi}"
esxi_iso_dir="${BKC_ESXI_ISO_DIR:-/vmfs/volumes/datastore1/auzix-isos}"
ssh_config="${BKC_SSH_CONFIG:-/home/auzieman/Projects/BlackKnightController/ops/workstation-ssh/bkc-lab.conf}"

primary_iso="${AUZIX_PRIMARY_ISO:-auzix-live-installer-current-${run_id}.iso}"
extra_isos="${AUZIX_EXTRA_ISOS:-auzix-live-desktop-current-${run_id}.iso}"

ssh_esxi() {
  sshpass -e ssh \
    -F "${ssh_config}" \
    -o PreferredAuthentications=password,keyboard-interactive \
    -o PubkeyAuthentication=no \
    -o StrictHostKeyChecking=no \
    "${esxi_host}" "$@"
}

stage_one() {
  iso="$1"
  echo "[auzix-esxi-stage] staging ${iso}"
  ssh "${source_host}" "test -s '${source_dir}/${iso}' && test -s '${source_dir}/${iso}.sha256'"
  ssh_esxi "mkdir -p '${esxi_iso_dir}'"
  ssh "${source_host}" "cat '${source_dir}/${iso}'" |
    ssh_esxi "cat > '${esxi_iso_dir}/${iso}'"
  ssh "${source_host}" "cat '${source_dir}/${iso}.sha256'" |
    ssh_esxi "cat > '${esxi_iso_dir}/${iso}.sha256'"
  ssh_esxi "ls -lh '${esxi_iso_dir}/${iso}' '${esxi_iso_dir}/${iso}.sha256'"
}

stage_one "${primary_iso}"
for iso in ${extra_isos}; do
  stage_one "${iso}"
done

echo "[auzix-esxi-stage] staged current AUZiX ISO set for run ${run_id}"

#!/bin/sh
set -eu

iso_name="${AUZIX_ISO_NAME:?AUZIX_ISO_NAME is required}"
vm_name="${BKC_ESXI_VM_NAME:-auzix-esxi-workstation-media-01}"
esxi_host="${BKC_ESXI_HOST:-lab-esxi}"
esxi_datastore="${BKC_ESXI_DATASTORE:-datastore1}"
esxi_iso_dir="${BKC_ESXI_ISO_DIR:-/vmfs/volumes/datastore1/auzix-isos}"
ssh_config="${BKC_SSH_CONFIG:-/home/auzieman/Projects/BlackKnightController/ops/workstation-ssh/bkc-lab.conf}"

ssh_esxi() {
  sshpass -e ssh \
    -F "${ssh_config}" \
    -o PreferredAuthentications=password,keyboard-interactive \
    -o PubkeyAuthentication=no \
    -o StrictHostKeyChecking=no \
    "${esxi_host}" "$@"
}

remote_script='
set -eu
iso_name="$1"
vm_name="$2"
datastore="$3"
iso_dir="$4"
iso_path="${iso_dir}/${iso_name}"
test -s "${iso_path}"
vmid="$(vim-cmd vmsvc/getallvms | awk -v n="${vm_name}" '\''$2==n {print $1; exit}'\'')"
test -n "${vmid}"
vmx="/vmfs/volumes/${datastore}/${vm_name}/${vm_name}.vmx"
test -s "${vmx}"
backup="${vmx}.bkc-auzix-$(date -u +%Y%m%dT%H%M%SZ).bak"
cp "${vmx}" "${backup}"
if ! grep -q "^sata0:1.present" "${vmx}"; then
  echo "sata0:1.present = \"TRUE\"" >> "${vmx}"
fi
if grep -q "^sata0:1.fileName" "${vmx}"; then
  sed -i "s#^sata0:1.fileName = .*#sata0:1.fileName = \"[${datastore}] auzix-isos/${iso_name}\"#" "${vmx}"
else
  echo "sata0:1.fileName = \"[${datastore}] auzix-isos/${iso_name}\"" >> "${vmx}"
fi
if grep -q "^sata0:1.deviceType" "${vmx}"; then
  sed -i "s#^sata0:1.deviceType = .*#sata0:1.deviceType = \"cdrom-image\"#" "${vmx}"
else
  echo "sata0:1.deviceType = \"cdrom-image\"" >> "${vmx}"
fi
if grep -q "^sata0:1.startConnected" "${vmx}"; then
  sed -i "s#^sata0:1.startConnected = .*#sata0:1.startConnected = \"TRUE\"#" "${vmx}"
else
  echo "sata0:1.startConnected = \"TRUE\"" >> "${vmx}"
fi
if grep -q "^sata0:1.clientDevice" "${vmx}"; then
  sed -i "s#^sata0:1.clientDevice = .*#sata0:1.clientDevice = \"FALSE\"#" "${vmx}"
fi
vim-cmd vmsvc/power.off "${vmid}" >/dev/null 2>&1 || true
vim-cmd vmsvc/reload "${vmid}" >/dev/null
vim-cmd vmsvc/power.on "${vmid}" >/dev/null
sleep 8
echo "vmid=${vmid}"
echo "vm=${vm_name}"
echo "iso=${iso_path}"
echo "vmx_backup=${backup}"
vim-cmd vmsvc/power.getstate "${vmid}" || true
tail -160 "/vmfs/volumes/${datastore}/${vm_name}/serial.log" 2>/dev/null || true
'

ssh_esxi "cat >/tmp/auzix-boot-current-iso.sh <<'BKC_AUZIX_ESXI_BOOT'
${remote_script}
BKC_AUZIX_ESXI_BOOT
chmod +x /tmp/auzix-boot-current-iso.sh
/bin/sh /tmp/auzix-boot-current-iso.sh '${iso_name}' '${vm_name}' '${esxi_datastore}' '${esxi_iso_dir}'"

#!/bin/sh
set -eu

fragment=${BKC_PROXMOX_DHCP_FRAGMENT:-/etc/dhcp/dhcpd.d/bkc-server2-proxmox-one-shot.conf}
main=${BKC_PROXMOX_DHCP_MAIN:-/etc/dhcp/dhcpd.conf}

# The parent dhcpd configuration includes this path explicitly, so retain an
# empty include instead of deleting it. This makes disarming idempotent and
# prevents a successfully installed host from entering the installer again.
install -m 0644 /dev/null "$fragment"
command -v restorecon >/dev/null 2>&1 && restorecon "$fragment" || true

dhcpd -t -cf "$main"
systemctl restart dhcpd
systemctl is-active --quiet dhcpd

echo "proxmox_one_shot_pxe=disarmed fragment=$fragment"

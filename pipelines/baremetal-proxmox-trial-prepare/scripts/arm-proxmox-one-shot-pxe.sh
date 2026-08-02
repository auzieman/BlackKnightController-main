#!/bin/sh
set -eu

mac=${BKC_PROXMOX_MAC:?BKC_PROXMOX_MAC is required}
lease=${BKC_PROXMOX_LEASE:?BKC_PROXMOX_LEASE is required}
http_host=${BKC_PROXMOX_HTTP_HOST:?BKC_PROXMOX_HTTP_HOST is required}
iso_path=${BKC_PROXMOX_ISO_PATH:?BKC_PROXMOX_ISO_PATH is required}
iso_url=${BKC_PROXMOX_ISO_URL:?BKC_PROXMOX_ISO_URL is required}
iso_sha256=${BKC_PROXMOX_ISO_SHA256:?BKC_PROXMOX_ISO_SHA256 is required}
pxe_kernel_url=${BKC_PROXMOX_KERNEL_URL:-http://$http_host/pxe/proxmox/9.2-1/pxeboot/linux26}
pxe_initrd_url=${BKC_PROXMOX_INITRD_URL:-http://$http_host/pxe/proxmox/9.2-1/pxeboot/initrd}
ipxe_path=${BKC_PROXMOX_IPXE_PATH:-/srv/pxe/server2-proxmox.ipxe}
fragment=${BKC_PROXMOX_DHCP_FRAGMENT:-/etc/dhcp/dhcpd.d/bkc-server2-proxmox-one-shot.conf}
main=${BKC_PROXMOX_DHCP_MAIN:-/etc/dhcp/dhcpd.conf}

case "$mac" in
  20:04:0f:e9:70:40) ;;
  *) echo "refusing unexpected Server2 MAC: $mac" >&2; exit 2 ;;
esac

test -s "$iso_path"
printf '%s  %s\n' "$iso_sha256" "$iso_path" | sha256sum -c -
curl -fsSI "$iso_url" >/dev/null
curl -fsSI "$pxe_kernel_url" >/dev/null
curl -fsSI "$pxe_initrd_url" >/dev/null

ipxe_tmp="${ipxe_path}.bkc-new"
fragment_tmp="${fragment}.bkc-new"

{
  echo '#!ipxe'
  echo '# BKC Server2 one-shot Proxmox auto-install ISO.'
  echo 'dhcp'
  printf 'kernel %s ro ramdisk_size=16777216 rw splash=verbose proxmox-start-auto-installer console=tty0 console=ttyS1,115200n8\n' "$pxe_kernel_url"
  printf 'initrd %s\n' "$pxe_initrd_url"
  echo 'boot'
} >"$ipxe_tmp"

{
  echo '# BKC one-shot Proxmox ISO lane. Exact Server2 LOM only.'
  echo 'host r630-proxmox-01-pxe {'
  printf '  hardware ethernet %s;\n' "$mac"
  printf '  fixed-address %s;\n' "$lease"
  printf '  next-server %s;\n' "$http_host"
  echo '  if exists user-class and option user-class = "iPXE" {'
  printf '    filename "http://%s/pxe/server2-proxmox.ipxe";\n' "$http_host"
  printf '    option bootfile-name "http://%s/pxe/server2-proxmox.ipxe";\n' "$http_host"
  echo '  } else {'
  echo '    filename "undionly.kpxe";'
  echo '    option bootfile-name "undionly.kpxe";'
  echo '  }'
  echo '}'
} >"$fragment_tmp"

install -m 0644 "$ipxe_tmp" "$ipxe_path"
install -m 0644 "$fragment_tmp" "$fragment"
rm -f "$ipxe_tmp" "$fragment_tmp"
command -v restorecon >/dev/null 2>&1 && restorecon "$ipxe_path" "$fragment" || true
command -v chcon >/dev/null 2>&1 && chcon --reference="$iso_path" "$ipxe_path" || true

include_line="include \"$fragment\";"
grep -Fq "$include_line" "$main" || printf '\n%s\n' "$include_line" >>"$main"

# Retire only the superseded Server2 experiment include; retain its files as evidence.
sed -i '\|include "/etc/dhcp/dhcpd.d/bkc-esxi-one-shot.conf";|d' "$main"

dhcpd -t -cf "$main"
systemctl restart dhcpd
systemctl is-active --quiet dhcpd
curl -fsS "http://$http_host/pxe/server2-proxmox.ipxe"
echo "proxmox_one_shot_pxe=armed mac=$mac lease=$lease iso_sha256=$iso_sha256"

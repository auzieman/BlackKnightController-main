#!/usr/bin/env bash
set -euo pipefail

management_cidr="${BKC_MANAGEMENT_NETWORK_CIDR:-192.168.1.0/24}"
provisioning_address="${BKC_PROVISIONING_ADDRESS:?BKC_PROVISIONING_ADDRESS is required}"

ip -json addr >/dev/null
ip -json route >/dev/null

if systemctl is-active --quiet dhcpd 2>/dev/null || systemctl is-active --quiet isc-dhcp-server 2>/dev/null; then
  echo "DHCP service is active before the PXE stage is approved." >&2
  exit 30
fi

if ! ip addr | grep -F -- "$provisioning_address" >/dev/null; then
  echo "Provisioning address ${provisioning_address} was not found." >&2
  exit 31
fi

default_route="$(ip route show default || true)"
case "$default_route" in
  *"192.168.1."*) ;;
  *)
    echo "Default route does not appear to stay on management network ${management_cidr}: ${default_route}" >&2
    exit 32
    ;;
esac

echo "Provisioning network check passed."

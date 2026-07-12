#!/usr/bin/env sh
set -eu

interface="${1:-${PROVISIONING_INTERFACE:-}}"
cidr="${2:-${PROVISIONING_CIDR:-10.20.0.0/24}}"

if [ -z "$interface" ]; then
  echo "provisioning interface is required" >&2
  exit 2
fi

ip -o addr show dev "$interface" | grep -q '10\.20\.0\.10/24'

if ip -o addr show dev "$interface" | grep -q '192\.168\.1\.'; then
  echo "refusing to bind DHCP to management network interface $interface" >&2
  exit 3
fi

if ss -lunp 2>/dev/null | grep -Eq ':(67|547)\b'; then
  echo "existing DHCP listener detected; inspect before continuing" >&2
  exit 4
fi

echo "dhcp-boundary-ok interface=$interface cidr=$cidr"

#!/usr/bin/env bash
set -euo pipefail

iface="${BKC_PROVISIONING_INTERFACE:?BKC_PROVISIONING_INTERFACE is required}"
address="${BKC_PROVISIONING_ADDRESS:?BKC_PROVISIONING_ADDRESS is required}"

current_default="$(ip route show default || true)"

nmcli device status | awk '{print $1}' | grep -Fx -- "$iface" >/dev/null

connection="bkc-provisioning-${iface}"
if ! nmcli -t -f NAME connection show | grep -Fx -- "$connection" >/dev/null; then
  nmcli connection add type ethernet ifname "$iface" con-name "$connection" ipv4.method manual ipv6.method disabled
fi

nmcli connection modify "$connection" ipv4.addresses "$address" ipv4.never-default yes
nmcli connection up "$connection"

after_default="$(ip route show default || true)"
if [ "$current_default" != "$after_default" ]; then
  echo "Default route changed while preparing provisioning interface." >&2
  exit 20
fi

ip addr show dev "$iface"


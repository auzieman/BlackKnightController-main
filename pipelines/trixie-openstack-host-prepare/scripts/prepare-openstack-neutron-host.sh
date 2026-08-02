#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This script must run as root." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

packages="${BKC_OPENSTACK_PACKAGES:-crudini curl jq iproute2 conntrack ipset ebtables bridge-utils vlan openvswitch-switch neutron-common neutron-openvswitch-agent python3-openstackclient python3-neutronclient}"
mode="${BKC_NEUTRON_MODE:-ovs}"
external_bridge="${BKC_NEUTRON_EXTERNAL_BRIDGE:-br-ex}"
physnet="${BKC_NEUTRON_PHYSNET:-physnet1}"
enable_network_config="${BKC_ENABLE_NETWORK_CONFIG:-false}"
smoke_mode="${BKC_OPENSTACK_SMOKE_MODE:-false}"

apt-get update
# shellcheck disable=SC2086
apt-get install -y --no-install-recommends $packages

install -d -m 0755 /etc/modules-load.d /etc/sysctl.d /etc/neutron /var/lib/bkc

cat >/etc/modules-load.d/bkc-openstack-neutron.conf <<'EOF'
br_netfilter
openvswitch
8021q
EOF

if [[ "${smoke_mode}" == "true" ]]; then
  modprobe br_netfilter || echo "smoke mode: br_netfilter module unavailable"
  modprobe openvswitch || echo "smoke mode: openvswitch module unavailable"
  modprobe 8021q || echo "smoke mode: 8021q module unavailable"
else
  modprobe br_netfilter
  modprobe openvswitch
  modprobe 8021q
fi

cat >/etc/sysctl.d/99-bkc-openstack-neutron.conf <<'EOF'
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward = 1
EOF
sysctl --system >/dev/null

systemctl enable --now openvswitch-switch

if [[ "${enable_network_config}" == "true" ]]; then
  ovs-vsctl --may-exist add-br "${external_bridge}"
fi

if command -v crudini >/dev/null 2>&1 && [[ -f /etc/neutron/plugins/ml2/openvswitch_agent.ini ]]; then
  crudini --set /etc/neutron/plugins/ml2/openvswitch_agent.ini ovs bridge_mappings "${physnet}:${external_bridge}"
  crudini --set /etc/neutron/plugins/ml2/openvswitch_agent.ini agent tunnel_types vxlan
  crudini --set /etc/neutron/plugins/ml2/openvswitch_agent.ini securitygroup enable_security_group true
fi

# The agent cannot start correctly until the OpenStack control plane supplies
# RabbitMQ/auth/ML2 settings, so leave it disabled after package installation.
systemctl disable neutron-openvswitch-agent 2>/dev/null || true
systemctl stop neutron-openvswitch-agent 2>/dev/null || true

cat >/var/lib/bkc/openstack-neutron-host-prep.json <<EOF
{
  "mode": "${mode}",
  "external_bridge": "${external_bridge}",
  "physnet": "${physnet}",
  "smoke_mode": "${smoke_mode}",
  "network_config_enabled": "${enable_network_config}",
  "agent_activation": "deferred-to-openstack-installer-provider"
}
EOF

echo "BKC OpenStack Neutron host preparation complete."

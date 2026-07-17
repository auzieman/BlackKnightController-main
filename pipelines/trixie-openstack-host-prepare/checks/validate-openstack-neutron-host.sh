#!/usr/bin/env bash
set -euo pipefail

required_commands=(ip ovs-vsctl sysctl jq)
for command_name in "${required_commands[@]}"; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "missing command: ${command_name}" >&2
    exit 1
  }
done

required_packages=(
  openvswitch-switch
  neutron-common
  neutron-openvswitch-agent
  python3-openstackclient
)
for package_name in "${required_packages[@]}"; do
  dpkg-query -W -f='${Status}' "${package_name}" 2>/dev/null | grep -q "install ok installed" || {
    echo "missing package: ${package_name}" >&2
    exit 1
  }
done

for module_name in br_netfilter openvswitch 8021q; do
  if ! lsmod | awk '{print $1}' | grep -qx "${module_name}"; then
    echo "module not currently loaded: ${module_name}" >&2
    exit 1
  fi
done

[[ "$(sysctl -n net.ipv4.ip_forward)" == "1" ]] || {
  echo "net.ipv4.ip_forward is not enabled" >&2
  exit 1
}

systemctl is-enabled openvswitch-switch >/dev/null
systemctl is-active openvswitch-switch >/dev/null
ovs-vsctl show >/dev/null

if [[ ! -f /var/lib/bkc/openstack-neutron-host-prep.json ]]; then
  echo "missing BKC OpenStack host-prep marker" >&2
  exit 1
fi

jq -e '.agent_activation == "deferred-to-openstack-installer-provider"' \
  /var/lib/bkc/openstack-neutron-host-prep.json >/dev/null

echo "BKC OpenStack Neutron host validation passed."

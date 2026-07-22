#!/usr/bin/env bash
set -euo pipefail
bkc_component_trap neutron

public_address="${BKC_OPENSTACK_PUBLIC_ADDRESS:-192.168.1.242}"
management_address="${BKC_OPENSTACK_MANAGEMENT_ADDRESS:-10.20.0.31}"
external_interface="${BKC_OPENSTACK_EXTERNAL_INTERFACE:-eno2}"
lab_password="${BKC_OPENSTACK_LAB_PASSWORD:-changeme123}"
metadata_secret="${BKC_OPENSTACK_METADATA_SECRET:-changeme123}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends \
  neutron-server neutron-openvswitch-agent neutron-l3-agent \
  neutron-dhcp-agent neutron-metadata-agent openvswitch-switch

. /root/admin-openrc

mariadb <<SQL
CREATE DATABASE IF NOT EXISTS neutron CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'neutron'@'localhost' IDENTIFIED BY '${lab_password}';
CREATE USER IF NOT EXISTS 'neutron'@'%' IDENTIFIED BY '${lab_password}';
ALTER USER 'neutron'@'localhost' IDENTIFIED BY '${lab_password}';
ALTER USER 'neutron'@'%' IDENTIFIED BY '${lab_password}';
GRANT ALL PRIVILEGES ON neutron.* TO 'neutron'@'localhost';
GRANT ALL PRIVILEGES ON neutron.* TO 'neutron'@'%';
FLUSH PRIVILEGES;
SQL

openstack user show neutron >/dev/null 2>&1 || openstack user create --domain default --password "$lab_password" neutron >/dev/null
openstack role add --project service --user neutron admin >/dev/null
openstack service show neutron >/dev/null 2>&1 || openstack service create --name neutron --description 'OpenStack Networking' network >/dev/null

ensure_endpoint() {
  interface="$1"; url="$2"
  openstack endpoint list --service neutron --interface "$interface" -f value -c ID | grep -q . || \
    openstack endpoint create --region RegionOne network "$interface" "$url" >/dev/null
}
ensure_endpoint public "http://${public_address}:9696"
ensure_endpoint internal "http://${management_address}:9696"
ensure_endpoint admin "http://${management_address}:9696"

conf=/etc/neutron/neutron.conf
crudini --set "$conf" database connection "mysql+pymysql://neutron:${lab_password}@${management_address}/neutron"
crudini --set "$conf" DEFAULT core_plugin ml2
crudini --set "$conf" DEFAULT service_plugins router
crudini --set "$conf" DEFAULT allow_overlapping_ips true
crudini --set "$conf" DEFAULT transport_url "rabbit://openstack:${lab_password}@${management_address}:5672/"
crudini --set "$conf" DEFAULT auth_strategy keystone
crudini --set "$conf" DEFAULT notify_nova_on_port_status_changes true
crudini --set "$conf" DEFAULT notify_nova_on_port_data_changes true
crudini --set "$conf" keystone_authtoken www_authenticate_uri "http://${management_address}:5000"
crudini --set "$conf" keystone_authtoken auth_url "http://${management_address}:5000"
crudini --set "$conf" keystone_authtoken memcached_servers "${management_address}:11211"
crudini --set "$conf" keystone_authtoken auth_type password
crudini --set "$conf" keystone_authtoken project_domain_name Default
crudini --set "$conf" keystone_authtoken user_domain_name Default
crudini --set "$conf" keystone_authtoken project_name service
crudini --set "$conf" keystone_authtoken username neutron
crudini --set "$conf" keystone_authtoken password "$lab_password"
crudini --set "$conf" keystone_authtoken region_name RegionOne
crudini --set "$conf" nova auth_url "http://${management_address}:5000"
crudini --set "$conf" nova auth_type password
crudini --set "$conf" nova project_domain_name Default
crudini --set "$conf" nova user_domain_name Default
crudini --set "$conf" nova region_name RegionOne
crudini --set "$conf" nova project_name service
crudini --set "$conf" nova username nova
crudini --set "$conf" nova password "$lab_password"
crudini --set "$conf" oslo_concurrency lock_path /var/lib/neutron/tmp

ml2=/etc/neutron/plugins/ml2/ml2_conf.ini
crudini --set "$ml2" ml2 type_drivers flat,vlan,vxlan
crudini --set "$ml2" ml2 tenant_network_types vxlan
crudini --set "$ml2" ml2 mechanism_drivers openvswitch,l2population
crudini --set "$ml2" ml2 extension_drivers port_security
crudini --set "$ml2" ml2_type_flat flat_networks provider
crudini --set "$ml2" ml2_type_vxlan vni_ranges 1:1000
crudini --set "$ml2" securitygroup enable_ipset true

ovs=/etc/neutron/plugins/ml2/openvswitch_agent.ini
crudini --set "$ovs" ovs bridge_mappings provider:br-ex
crudini --set "$ovs" ovs local_ip "$management_address"
crudini --set "$ovs" agent tunnel_types vxlan
crudini --set "$ovs" agent l2_population true
crudini --set "$ovs" securitygroup enable_security_group true
crudini --set "$ovs" securitygroup firewall_driver openvswitch

crudini --set /etc/neutron/l3_agent.ini DEFAULT interface_driver openvswitch
crudini --set /etc/neutron/dhcp_agent.ini DEFAULT interface_driver openvswitch
crudini --set /etc/neutron/dhcp_agent.ini DEFAULT dhcp_driver neutron.agent.linux.dhcp.Dnsmasq
crudini --set /etc/neutron/dhcp_agent.ini DEFAULT enable_isolated_metadata true
crudini --set /etc/neutron/metadata_agent.ini DEFAULT nova_metadata_host "$management_address"
crudini --set /etc/neutron/metadata_agent.ini DEFAULT metadata_proxy_shared_secret "$metadata_secret"

crudini --set /etc/nova/nova.conf neutron auth_url "http://${management_address}:5000"
crudini --set /etc/nova/nova.conf neutron auth_type password
crudini --set /etc/nova/nova.conf neutron project_domain_name Default
crudini --set /etc/nova/nova.conf neutron user_domain_name Default
crudini --set /etc/nova/nova.conf neutron region_name RegionOne
crudini --set /etc/nova/nova.conf neutron project_name service
crudini --set /etc/nova/nova.conf neutron username neutron
crudini --set /etc/nova/nova.conf neutron password "$lab_password"
crudini --set /etc/nova/nova.conf neutron service_metadata_proxy true
crudini --set /etc/nova/nova.conf neutron metadata_proxy_shared_secret "$metadata_secret"

su -s /bin/sh -c "neutron-db-manage --config-file ${conf} --config-file ${ml2} upgrade head" neutron

ovs-vsctl --may-exist add-br br-ex
ip link set br-ex up
ip link set "$external_interface" up
ovs-vsctl --may-exist add-port br-ex "$external_interface"

systemctl enable --now neutron-api neutron-rpc-server neutron-dhcp-agent neutron-metadata-agent neutron-l3-agent neutron-openvswitch-agent
systemctl restart neutron-api neutron-rpc-server neutron-dhcp-agent neutron-metadata-agent neutron-l3-agent neutron-openvswitch-agent nova-api nova-api-metadata nova-compute
for service in neutron-api neutron-rpc-server neutron-dhcp-agent neutron-metadata-agent neutron-l3-agent neutron-openvswitch-agent; do
  bkc_wait_service "$service" 300
done
bkc_wait_http neutron-api "http://${management_address}:9696/" 300
bkc_wait_until neutron-agent-registration 300 bash -c '. /root/admin-openrc; test "$(openstack network agent list -f value -c ID | wc -l)" -ge 3'

openstack network show lab-internal >/dev/null 2>&1 || openstack network create lab-internal >/dev/null
openstack subnet show lab-internal-v4 >/dev/null 2>&1 || openstack subnet create \
  --network lab-internal --subnet-range 172.24.10.0/24 --gateway 172.24.10.1 \
  --dns-nameserver 10.20.0.10 --dhcp lab-internal-v4 >/dev/null
openstack security group rule create --proto icmp default >/dev/null 2>&1 || true
openstack security group rule create --proto tcp --dst-port 22 default >/dev/null 2>&1 || true

openstack network agent list
openstack network show lab-internal

cat >/var/lib/bkc/native-openstack-neutron.json <<EOF
{"status":"ready","neutron":"ready","network":"lab-internal","subnet":"172.24.10.0/24","external_interface":"${external_interface}","external_carrier":"$(cat /sys/class/net/${external_interface}/carrier 2>/dev/null || echo 0)"}
EOF
echo "neutron=ready network=lab-internal external=${external_interface}"

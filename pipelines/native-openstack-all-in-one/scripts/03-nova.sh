#!/usr/bin/env bash
set -euo pipefail
bkc_component_trap nova

public_address="${BKC_OPENSTACK_PUBLIC_ADDRESS:-192.168.1.242}"
management_address="${BKC_OPENSTACK_MANAGEMENT_ADDRESS:-10.20.0.31}"
lab_password="${BKC_OPENSTACK_LAB_PASSWORD:-changeme123}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends \
  nova-api nova-conductor nova-scheduler nova-compute nova-novncproxy
sed -i 's/^NOVA_CONSOLE_PROXY_TYPE=.*/NOVA_CONSOLE_PROXY_TYPE=novnc/' /etc/default/nova-consoleproxy
sed -i 's/^NOVA_SERIAL_PROXY_START=.*/NOVA_SERIAL_PROXY_START=FALSE/' /etc/default/nova-consoleproxy
. /root/admin-openrc

mariadb <<SQL
CREATE DATABASE IF NOT EXISTS nova_api CHARACTER SET utf8mb4;
CREATE DATABASE IF NOT EXISTS nova CHARACTER SET utf8mb4;
CREATE DATABASE IF NOT EXISTS nova_cell0 CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'nova'@'localhost' IDENTIFIED BY '${lab_password}';
CREATE USER IF NOT EXISTS 'nova'@'%' IDENTIFIED BY '${lab_password}';
ALTER USER 'nova'@'localhost' IDENTIFIED BY '${lab_password}';
ALTER USER 'nova'@'%' IDENTIFIED BY '${lab_password}';
GRANT ALL PRIVILEGES ON nova_api.* TO 'nova'@'localhost';
GRANT ALL PRIVILEGES ON nova_api.* TO 'nova'@'%';
GRANT ALL PRIVILEGES ON nova.* TO 'nova'@'localhost';
GRANT ALL PRIVILEGES ON nova.* TO 'nova'@'%';
GRANT ALL PRIVILEGES ON nova_cell0.* TO 'nova'@'localhost';
GRANT ALL PRIVILEGES ON nova_cell0.* TO 'nova'@'%';
FLUSH PRIVILEGES;
SQL

openstack user show nova >/dev/null 2>&1 || openstack user create --domain default --password "$lab_password" nova >/dev/null
openstack role add --project service --user nova admin >/dev/null
openstack service show nova >/dev/null 2>&1 || openstack service create --name nova --description 'OpenStack Compute' compute >/dev/null

ensure_endpoint() {
  interface="$1"; url="$2"
  openstack endpoint list --service nova --interface "$interface" -f value -c ID | grep -q . || \
    openstack endpoint create --region RegionOne compute "$interface" "$url" >/dev/null
}
ensure_endpoint public "http://${public_address}:8774/v2.1"
ensure_endpoint internal "http://${management_address}:8774/v2.1"
ensure_endpoint admin "http://${management_address}:8774/v2.1"

conf=/etc/nova/nova.conf
crudini --set "$conf" DEFAULT transport_url "rabbit://openstack:${lab_password}@${management_address}:5672/"
crudini --set "$conf" DEFAULT my_ip "$management_address"
crudini --set "$conf" DEFAULT use_neutron true
crudini --set "$conf" DEFAULT firewall_driver nova.virt.firewall.NoopFirewallDriver
crudini --set "$conf" api_database connection "mysql+pymysql://nova:${lab_password}@${management_address}/nova_api"
crudini --set "$conf" database connection "mysql+pymysql://nova:${lab_password}@${management_address}/nova"
crudini --set "$conf" api auth_strategy keystone
crudini --set "$conf" keystone_authtoken www_authenticate_uri "http://${management_address}:5000/"
crudini --set "$conf" keystone_authtoken auth_url "http://${management_address}:5000/"
crudini --set "$conf" keystone_authtoken memcached_servers "${management_address}:11211"
crudini --set "$conf" keystone_authtoken auth_type password
crudini --set "$conf" keystone_authtoken project_domain_name Default
crudini --set "$conf" keystone_authtoken user_domain_name Default
crudini --set "$conf" keystone_authtoken project_name service
crudini --set "$conf" keystone_authtoken username nova
crudini --set "$conf" keystone_authtoken password "$lab_password"
crudini --set "$conf" keystone_authtoken region_name RegionOne
crudini --set "$conf" service_user send_service_user_token true
crudini --set "$conf" service_user auth_url "http://${management_address}:5000/v3"
crudini --set "$conf" service_user auth_strategy keystone
crudini --set "$conf" service_user auth_type password
crudini --set "$conf" service_user project_domain_name Default
crudini --set "$conf" service_user project_name service
crudini --set "$conf" service_user user_domain_name Default
crudini --set "$conf" service_user username nova
crudini --set "$conf" service_user password "$lab_password"
crudini --set "$conf" vnc enabled true
crudini --set "$conf" vnc server_listen 0.0.0.0
crudini --set "$conf" vnc server_proxyclient_address "$management_address"
crudini --set "$conf" vnc novncproxy_base_url "http://${public_address}:6080/vnc_auto.html"
crudini --set "$conf" spice enabled false
crudini --set "$conf" glance api_servers "http://${management_address}:9292"
crudini --set "$conf" oslo_concurrency lock_path /var/lib/nova/tmp
crudini --set "$conf" placement region_name RegionOne
crudini --set "$conf" placement project_domain_name Default
crudini --set "$conf" placement project_name service
crudini --set "$conf" placement auth_type password
crudini --set "$conf" placement user_domain_name Default
crudini --set "$conf" placement auth_url "http://${management_address}:5000/v3"
crudini --set "$conf" placement username placement
crudini --set "$conf" placement password "$lab_password"
crudini --set "$conf" libvirt virt_type kvm
crudini --set "$conf" libvirt cpu_mode host-model

su -s /bin/sh -c 'nova-manage api_db sync' nova
su -s /bin/sh -c 'nova-manage cell_v2 map_cell0' nova
if ! su -s /bin/sh -c 'nova-manage cell_v2 list_cells --verbose' nova | grep -q cell1; then
  su -s /bin/sh -c 'nova-manage cell_v2 create_cell --name=cell1 --verbose' nova
fi
su -s /bin/sh -c 'nova-manage db sync' nova

# Debian may briefly return a failed start while Nova's runtime directories and
# dependent services settle, even though systemd's restart policy recovers it
# seconds later. Enabling is synchronous; startup is a requested transition.
# The readiness loop below is the authoritative success/failure signal.
systemctl enable libvirtd nova-api nova-api-metadata nova-conductor nova-scheduler nova-compute nova-novncproxy
systemctl restart libvirtd nova-api nova-api-metadata nova-conductor nova-scheduler nova-compute nova-novncproxy || true
for service in libvirtd nova-api nova-api-metadata nova-conductor nova-scheduler nova-compute nova-novncproxy; do
  bkc_wait_service "$service" 300
done
bkc_wait_http nova-api "http://${management_address}:8774/" 300
bkc_wait_until nova-compute-registration 300 bash -c '. /root/admin-openrc; openstack compute service list -f value -c Binary | grep -Fxq nova-compute'
su -s /bin/sh -c 'nova-manage cell_v2 discover_hosts --verbose' nova || true

openstack flavor show bkc.small >/dev/null 2>&1 || openstack flavor create --ram 2048 --disk 20 --vcpus 2 bkc.small >/dev/null
openstack compute service list
nova-status upgrade check

cat >/var/lib/bkc/native-openstack-nova.json <<EOF
{"status":"ready","nova":"ready","compute_host":"r630-openstack-01","flavor":"bkc.small"}
EOF
echo "nova=ready compute=r630-openstack-01"

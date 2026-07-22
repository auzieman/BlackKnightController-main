#!/usr/bin/env bash
set -euo pipefail
bkc_component_trap glance-placement

public_address="${BKC_OPENSTACK_PUBLIC_ADDRESS:-192.168.1.242}"
management_address="${BKC_OPENSTACK_MANAGEMENT_ADDRESS:-10.20.0.31}"
lab_password="${BKC_OPENSTACK_LAB_PASSWORD:-changeme123}"
image_url="${BKC_OPENSTACK_IMAGE_URL:-https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2}"
image_path="${BKC_OPENSTACK_IMAGE_PATH:-/var/lib/bkc/openstack-images/debian-13-genericcloud-amd64.qcow2}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends glance-api placement-api

. /root/admin-openrc

mariadb <<SQL
CREATE DATABASE IF NOT EXISTS glance CHARACTER SET utf8mb4;
CREATE DATABASE IF NOT EXISTS placement CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'glance'@'localhost' IDENTIFIED BY '${lab_password}';
CREATE USER IF NOT EXISTS 'glance'@'%' IDENTIFIED BY '${lab_password}';
CREATE USER IF NOT EXISTS 'placement'@'localhost' IDENTIFIED BY '${lab_password}';
CREATE USER IF NOT EXISTS 'placement'@'%' IDENTIFIED BY '${lab_password}';
ALTER USER 'glance'@'localhost' IDENTIFIED BY '${lab_password}';
ALTER USER 'glance'@'%' IDENTIFIED BY '${lab_password}';
ALTER USER 'placement'@'localhost' IDENTIFIED BY '${lab_password}';
ALTER USER 'placement'@'%' IDENTIFIED BY '${lab_password}';
GRANT ALL PRIVILEGES ON glance.* TO 'glance'@'localhost';
GRANT ALL PRIVILEGES ON glance.* TO 'glance'@'%';
GRANT ALL PRIVILEGES ON placement.* TO 'placement'@'localhost';
GRANT ALL PRIVILEGES ON placement.* TO 'placement'@'%';
FLUSH PRIVILEGES;
SQL

openstack project show service >/dev/null 2>&1 || openstack project create --domain default service >/dev/null
for service_user in glance placement; do
  openstack user show "$service_user" >/dev/null 2>&1 || openstack user create --domain default --password "$lab_password" "$service_user" >/dev/null
  openstack role add --project service --user "$service_user" admin >/dev/null
done

openstack service show glance >/dev/null 2>&1 || openstack service create --name glance --description 'OpenStack Image' image >/dev/null
openstack service show placement >/dev/null 2>&1 || openstack service create --name placement --description 'OpenStack Placement' placement >/dev/null

ensure_endpoint() {
  service="$1"; interface="$2"; url="$3"
  openstack endpoint list --service "$service" --interface "$interface" -f value -c ID | grep -q . || \
    openstack endpoint create --region RegionOne "$service" "$interface" "$url" >/dev/null
}
ensure_endpoint glance public "http://${public_address}:9292"
ensure_endpoint glance internal "http://${management_address}:9292"
ensure_endpoint glance admin "http://${management_address}:9292"
ensure_endpoint placement public "http://${public_address}:8778"
ensure_endpoint placement internal "http://${management_address}:8778"
ensure_endpoint placement admin "http://${management_address}:8778"

crudini --set /etc/glance/glance-api.conf database connection "mysql+pymysql://glance:${lab_password}@${management_address}/glance"
crudini --set /etc/glance/glance-api.conf keystone_authtoken www_authenticate_uri "http://${management_address}:5000"
crudini --set /etc/glance/glance-api.conf keystone_authtoken auth_url "http://${management_address}:5000"
crudini --set /etc/glance/glance-api.conf keystone_authtoken memcached_servers "${management_address}:11211"
crudini --set /etc/glance/glance-api.conf keystone_authtoken auth_type password
crudini --set /etc/glance/glance-api.conf keystone_authtoken project_domain_name Default
crudini --set /etc/glance/glance-api.conf keystone_authtoken user_domain_name Default
crudini --set /etc/glance/glance-api.conf keystone_authtoken project_name service
crudini --set /etc/glance/glance-api.conf keystone_authtoken username glance
crudini --set /etc/glance/glance-api.conf keystone_authtoken password "$lab_password"
crudini --set /etc/glance/glance-api.conf keystone_authtoken region_name RegionOne
crudini --set /etc/glance/glance-api.conf paste_deploy flavor keystone
crudini --set /etc/glance/glance-api.conf DEFAULT bind_host 0.0.0.0
crudini --set /etc/glance/glance-api.conf DEFAULT enabled_backends fs:file
crudini --set /etc/glance/glance-api.conf glance_store default_backend fs
crudini --set /etc/glance/glance-api.conf fs filesystem_store_datadir /var/lib/glance/images
su -s /bin/sh -c 'glance-manage db_sync' glance

crudini --set /etc/placement/placement.conf placement_database connection "mysql+pymysql://placement:${lab_password}@${management_address}/placement"
crudini --set /etc/placement/placement.conf api auth_strategy keystone
crudini --set /etc/placement/placement.conf keystone_authtoken auth_url "http://${management_address}:5000/v3"
crudini --set /etc/placement/placement.conf keystone_authtoken memcached_servers "${management_address}:11211"
crudini --set /etc/placement/placement.conf keystone_authtoken auth_type password
crudini --set /etc/placement/placement.conf keystone_authtoken project_domain_name Default
crudini --set /etc/placement/placement.conf keystone_authtoken user_domain_name Default
crudini --set /etc/placement/placement.conf keystone_authtoken project_name service
crudini --set /etc/placement/placement.conf keystone_authtoken username placement
crudini --set /etc/placement/placement.conf keystone_authtoken password "$lab_password"
crudini --set /etc/placement/placement.conf keystone_authtoken region_name RegionOne
su -s /bin/sh -c 'placement-manage db sync' placement

systemctl enable --now glance-api placement-api
systemctl restart glance-api placement-api apache2
bkc_wait_service glance-api 240
bkc_wait_service placement-api 240
bkc_wait_http glance "http://${management_address}:9292/" 240
bkc_wait_http placement "http://${management_address}:8778/" 240

install -d -m 0755 /var/lib/bkc/openstack-images
if [ ! -s "$image_path" ]; then
  curl -fL --retry 3 -o "${image_path}.part" "$image_url"
  mv "${image_path}.part" "$image_path"
fi
if ! openstack image show debian-13-genericcloud >/dev/null 2>&1; then
  openstack image create debian-13-genericcloud --file "$image_path" --disk-format qcow2 --container-format bare --public >/dev/null
fi

openstack image show debian-13-genericcloud -f value -c status | grep -Fx active
placement-status upgrade check
curl -fsS "http://${public_address}:9292/" >/dev/null

cat >/var/lib/bkc/native-openstack-image-placement.json <<EOF
{"status":"ready","glance":"ready","placement":"ready","image":"debian-13-genericcloud"}
EOF
echo "glance=ready placement=ready image=active"

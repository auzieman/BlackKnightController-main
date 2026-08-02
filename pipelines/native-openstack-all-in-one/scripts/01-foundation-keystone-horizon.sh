#!/usr/bin/env bash
set -euo pipefail
trap 'rc=$?; echo "foundation_failed line=${LINENO} rc=${rc}" >&2' ERR
bkc_component_trap foundation

public_address="${BKC_OPENSTACK_PUBLIC_ADDRESS:-192.168.1.242}"
management_address="${BKC_OPENSTACK_MANAGEMENT_ADDRESS:-10.20.0.31}"
management_cidr="${BKC_OPENSTACK_MANAGEMENT_CIDR:-10.20.0.31/24}"
internal_vip="${BKC_OPENSTACK_INTERNAL_VIP:-10.20.0.30}"
parent_interface="${BKC_OPENSTACK_PARENT_INTERFACE:-eno1}"
management_interface="${BKC_OPENSTACK_MANAGEMENT_INTERFACE:-bkc-mgmt0}"
management_gateway="${BKC_OPENSTACK_MANAGEMENT_GATEWAY:-10.20.0.9}"
lab_password="${BKC_OPENSTACK_LAB_PASSWORD:-changeme123}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 2
fi

install -d -m 0755 /var/lib/bkc /etc/systemd/system

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  mariadb-server rabbitmq-server memcached python3-pymysql \
  keystone crudini python3-openstackclient \
  openstack-dashboard-apache ssl-cert
install -d -m 0755 /etc/mysql/mariadb.conf.d
a2enmod ssl >/dev/null
if ! grep -Fq 'WSGIApplicationGroup %{GLOBAL}' /etc/apache2/sites-available/openstack-dashboard-alias-only.conf; then
  printf '\nWSGIApplicationGroup %%{GLOBAL}\n' >>/etc/apache2/sites-available/openstack-dashboard-alias-only.conf
fi

cat >/usr/local/sbin/bkc-openstack-management-network <<EOF
#!/bin/sh
set -eu
ip link show ${management_interface} >/dev/null 2>&1 || ip link add ${management_interface} link ${parent_interface} type macvlan mode bridge
ip link set ${management_interface} up
ip -4 -o address show dev ${management_interface} | awk '{print \$4}' | grep -Fxq ${management_cidr} || ip address add ${management_cidr} dev ${management_interface}
ip -4 -o address show dev ${management_interface} | awk '{print \$4}' | cut -d/ -f1 | grep -Fxq ${internal_vip} || ip address add ${internal_vip}/32 dev ${management_interface}
ip -4 -o address show dev ${management_interface} | awk '{print \$4}' | cut -d/ -f1 | grep -Fxq ${public_address} || ip address add ${public_address}/32 dev ${management_interface}
ip route replace default via ${management_gateway} dev ${management_interface}
EOF
chmod 0755 /usr/local/sbin/bkc-openstack-management-network

cat >/etc/systemd/system/bkc-openstack-management-network.service <<EOF
[Unit]
Description=BlackKnight OpenStack management network
After=network-online.target
Wants=network-online.target
Before=mariadb.service rabbitmq-server.service memcached.service keystone.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/bkc-openstack-management-network
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now bkc-openstack-management-network.service

cat >/etc/mysql/mariadb.conf.d/90-bkc-openstack.cnf <<EOF
[mysqld]
bind-address = ${management_address}
default-storage-engine = innodb
innodb_file_per_table = on
max_connections = 4096
collation-server = utf8mb4_general_ci
character-set-server = utf8mb4
EOF
systemctl restart mariadb

sed -i -E "s/^-l .*/-l 127.0.0.1,${management_address}/" /etc/memcached.conf
systemctl restart memcached

rabbitmqctl add_user openstack "$lab_password" >/dev/null 2>&1 || rabbitmqctl change_password openstack "$lab_password" >/dev/null
rabbitmqctl set_permissions openstack '.*' '.*' '.*' >/dev/null

mariadb <<SQL
CREATE DATABASE IF NOT EXISTS keystone CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'keystone'@'localhost' IDENTIFIED BY '${lab_password}';
CREATE USER IF NOT EXISTS 'keystone'@'%' IDENTIFIED BY '${lab_password}';
ALTER USER 'keystone'@'localhost' IDENTIFIED BY '${lab_password}';
ALTER USER 'keystone'@'%' IDENTIFIED BY '${lab_password}';
GRANT ALL PRIVILEGES ON keystone.* TO 'keystone'@'localhost';
GRANT ALL PRIVILEGES ON keystone.* TO 'keystone'@'%';
FLUSH PRIVILEGES;
SQL

crudini --set /etc/keystone/keystone.conf database connection "mysql+pymysql://keystone:${lab_password}@${management_address}/keystone"
crudini --set /etc/keystone/keystone.conf token provider fernet
su -s /bin/sh -c 'keystone-manage db_sync' keystone
keystone-manage fernet_setup --keystone-user keystone --keystone-group keystone
keystone-manage credential_setup --keystone-user keystone --keystone-group keystone
keystone-manage bootstrap \
  --bootstrap-password "$lab_password" \
  --bootstrap-admin-url "http://${management_address}:5000/v3/" \
  --bootstrap-internal-url "http://${management_address}:5000/v3/" \
  --bootstrap-public-url "http://${public_address}:5000/v3/" \
  --bootstrap-region-id RegionOne
systemctl enable keystone >/dev/null 2>&1 || true
systemctl restart keystone
bkc_wait_service keystone 180
bkc_wait_http keystone "http://${management_address}:5000/v3/" 180

cat >/root/admin-openrc <<EOF
export OS_USERNAME=admin
export OS_PASSWORD=${lab_password}
export OS_PROJECT_NAME=admin
export OS_USER_DOMAIN_NAME=Default
export OS_PROJECT_DOMAIN_NAME=Default
export OS_AUTH_URL=http://${public_address}:5000/v3
export OS_IDENTITY_API_VERSION=3
EOF
chmod 0600 /root/admin-openrc
. /root/admin-openrc
openstack role show member >/dev/null 2>&1 || openstack role create member >/dev/null
openstack role add --user admin --project admin member >/dev/null 2>&1 || true

python3 - "$public_address" <<'PY'
from pathlib import Path
import re
import sys

address = sys.argv[1]
allowed_hosts = [
    address,
    'r630-openstack-01',
    'r630-openstack-01.lab.auzietek.com',
    'swarm1.lab.auzietek.com',
    '192.168.1.15',
    'localhost',
]
csrf_trusted_origins = [
    'http://swarm1.lab.auzietek.com:8082',
    'http://192.168.1.15:8082',
    f'http://{address}',
]
path = Path('/etc/openstack-dashboard/local_settings.py')
text = path.read_text()
text = re.sub(r'^ALLOWED_HOSTS\s*=.*$', f"ALLOWED_HOSTS = {allowed_hosts!r}", text, flags=re.M)
text = re.sub(r'^OPENSTACK_HOST\s*=.*$', f'OPENSTACK_HOST = "{address}"', text, flags=re.M)
text = re.sub(r'^OPENSTACK_KEYSTONE_URL\s*=.*$', 'OPENSTACK_KEYSTONE_URL = "http://%s:5000/v3" % OPENSTACK_HOST', text, flags=re.M)
if re.search(r'^CSRF_TRUSTED_ORIGINS\s*=', text, flags=re.M):
    text = re.sub(r'^CSRF_TRUSTED_ORIGINS\s*=.*$', f"CSRF_TRUSTED_ORIGINS = {csrf_trusted_origins!r}", text, flags=re.M)
else:
    text += f"\nCSRF_TRUSTED_ORIGINS = {csrf_trusted_origins!r}\n"
if re.search(r'^USE_X_FORWARDED_HOST\s*=', text, flags=re.M):
    text = re.sub(r'^USE_X_FORWARDED_HOST\s*=.*$', 'USE_X_FORWARDED_HOST = True', text, flags=re.M)
else:
    text += "\nUSE_X_FORWARDED_HOST = True\n"
single_domain_settings = {
    'OPENSTACK_KEYSTONE_MULTIDOMAIN_SUPPORT': 'False',
    'OPENSTACK_KEYSTONE_DEFAULT_DOMAIN': "'Default'",
    'OPENSTACK_KEYSTONE_DEFAULT_ROLE': "'member'",
}
for key, value in single_domain_settings.items():
    if re.search(rf'^{key}\s*=', text, flags=re.M):
        text = re.sub(rf'^{key}\s*=.*$', f'{key} = {value}', text, flags=re.M)
    else:
        text += f"\n{key} = {value}\n"
path.write_text(text)
PY

systemctl restart apache2
bkc_wait_service apache2 180
. /root/admin-openrc
openstack token issue -f json >/tmp/bkc-openstack-token.json
curl -kfsSL --max-redirs 5 "http://${public_address}/horizon/" >/tmp/bkc-horizon.html
grep -Eqi 'OpenStack|Horizon|Log in' /tmp/bkc-horizon.html

cat >/var/lib/bkc/native-openstack-foundation.json <<EOF
{"status":"ready","public_address":"${public_address}","management_address":"${management_address}","internal_vip":"${internal_vip}","keystone":"ready","horizon":"ready"}
EOF

echo "foundation=ready"
echo "keystone=http://${public_address}:5000/v3"
echo "horizon=http://${public_address}/horizon/"

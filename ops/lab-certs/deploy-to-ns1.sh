#!/usr/bin/env bash
set -euo pipefail

target="${1:-root@192.168.1.10}"
remote_dir="${BKC_LAB_CERT_REMOTE_DIR:-/opt/bkc-lab-certs}"

ssh -o BatchMode=yes "$target" "install -d -m 0755 '$remote_dir' /root/.secrets"
scp -q ops/remote-sites/ionos_dns.py \
  ops/lab-certs/certbot-ionos-auth.sh \
  ops/lab-certs/certbot-ionos-cleanup.sh \
  ops/lab-certs/apply-and-validate-lab-map.sh \
  ops/lab-certs/request-lab-auzietek-cert.sh \
  ops/lab-certs/lab-auzietek-map.json \
  "$target:$remote_dir/"
ssh -o BatchMode=yes "$target" "chmod 0755 '$remote_dir'/ionos_dns.py '$remote_dir'/*.sh && chmod 0700 /root/.secrets"

echo "Installed lab DNS/cert helpers on $target:$remote_dir"
echo "Next: create /root/.secrets/ionos-api-key on ns1 with mode 0600, then run:"
echo "  python3 $remote_dir/ionos_dns.py --api-key-file /root/.secrets/ionos-api-key snapshot --scope lab.auzietek.com"
echo "  $remote_dir/apply-and-validate-lab-map.sh"

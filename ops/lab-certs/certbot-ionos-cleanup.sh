#!/usr/bin/env bash
set -euo pipefail

api_key_file="${IONOS_API_KEY_FILE:-/root/.secrets/ionos-api-key}"
helper="${IONOS_DNS_HELPER:-/opt/bkc-lab-certs/ionos_dns.py}"
record_name="_acme-challenge.${CERTBOT_DOMAIN}"

python3 "$helper" \
  --api-key-file "$api_key_file" \
  delete-txt \
  --name "$record_name" \
  --content "$CERTBOT_VALIDATION" >/dev/null

#!/usr/bin/env bash
set -euo pipefail

email="${LETSENCRYPT_EMAIL:-admin@auzietek.com}"
cert_name="${LAB_CERT_NAME:-lab.auzietek.com}"
auth_hook="${IONOS_AUTH_HOOK:-/opt/bkc-lab-certs/certbot-ionos-auth.sh}"
cleanup_hook="${IONOS_CLEANUP_HOOK:-/opt/bkc-lab-certs/certbot-ionos-cleanup.sh}"

if ! command -v certbot >/dev/null 2>&1; then
  echo "certbot is not installed. Install it on ns1 before requesting certificates." >&2
  exit 2
fi

certbot certonly \
  --manual \
  --preferred-challenges dns \
  --manual-auth-hook "$auth_hook" \
  --manual-cleanup-hook "$cleanup_hook" \
  --agree-tos \
  --non-interactive \
  --email "$email" \
  --cert-name "$cert_name" \
  -d "lab.auzietek.com" \
  -d "*.lab.auzietek.com"

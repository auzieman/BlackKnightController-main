#!/usr/bin/env bash
set -euo pipefail

portainer_url="${PORTAINER_URL:-https://127.0.0.1:9443}"
portainer_user="${PORTAINER_USER:-admin}"
endpoint_id="${PORTAINER_ENDPOINT_ID:-6}"
image="${BKC_PROMOTE_IMAGE:-192.168.1.15:5001/blackknightcontroller/bkc:pipeline-explainer-drawio-20260802100435}"
services_csv="${BKC_PROMOTE_SERVICES:-bkc-alt_bkc,bkc-alt_worker}"

if [[ -z "${PORTAINER_PASSWORD:-}" ]]; then
  echo "PORTAINER_PASSWORD is required in the runtime environment." >&2
  exit 2
fi

token="$(
  curl -ksS -X POST "${portainer_url}/api/auth" \
    -H "Content-Type: application/json" \
    --data "{\"Username\":\"${portainer_user}\",\"Password\":\"${PORTAINER_PASSWORD}\"}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("jwt",""))'
)"

if [[ -z "${token}" ]]; then
  echo "Portainer authentication did not return a token." >&2
  exit 3
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

IFS=',' read -r -a wanted_services <<< "${services_csv}"

curl -ksS "${portainer_url}/api/endpoints/${endpoint_id}/docker/services" \
  -H "Authorization: Bearer ${token}" > "${tmpdir}/services.json"

python3 - "${tmpdir}/services.json" "${tmpdir}/service-map.tsv" "${wanted_services[@]}" <<'PY'
import json
import sys

services = json.load(open(sys.argv[1]))
out = open(sys.argv[2], "w")
wanted = set(sys.argv[3:])
seen = set()
for svc in services:
    name = svc.get("Spec", {}).get("Name", "")
    if name in wanted:
        seen.add(name)
        out.write(f"{name}\t{svc.get('ID')}\t{svc.get('Version', {}).get('Index')}\n")
missing = sorted(wanted - seen)
if missing:
    raise SystemExit(f"Missing services: {', '.join(missing)}")
PY

while IFS=$'\t' read -r service_name service_id version; do
  echo "promote_service name=${service_name} image=${image}"
  curl -ksS "${portainer_url}/api/endpoints/${endpoint_id}/docker/services/${service_id}" \
    -H "Authorization: Bearer ${token}" > "${tmpdir}/${service_name}.json"
  python3 - "${tmpdir}/${service_name}.json" "${tmpdir}/${service_name}.update.json" "${image}" <<'PY'
import json
import sys

src, dst, image = sys.argv[1:]
service = json.load(open(src))
spec = service["Spec"]
spec["TaskTemplate"]["ContainerSpec"]["Image"] = image
json.dump(spec, open(dst, "w"), separators=(",", ":"))
PY
  curl -ksS -X POST \
    "${portainer_url}/api/endpoints/${endpoint_id}/docker/services/${service_id}/update?version=${version}" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    --data-binary @"${tmpdir}/${service_name}.update.json" >/dev/null
done < "${tmpdir}/service-map.tsv"

echo "promotion_requested image=${image}"

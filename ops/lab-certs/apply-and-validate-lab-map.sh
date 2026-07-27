#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
helper="${IONOS_DNS_HELPER:-$script_dir/ionos_dns.py}"
map_file="${LAB_DNS_MAP_FILE:-$script_dir/lab-auzietek-map.json}"
api_key_file="${IONOS_API_KEY_FILE:-/root/.secrets/ionos-api-key}"
scope="${LAB_DNS_SCOPE:-lab.auzietek.com}"

if [[ ! -s "$api_key_file" && -z "${IONOS_API_KEY:-}" ]]; then
  echo "Missing IONOS API key. Create $api_key_file mode 0600 or set IONOS_API_KEY." >&2
  exit 2
fi

key_args=()
if [[ -s "$api_key_file" ]]; then
  key_args=(--api-key-file "$api_key_file")
fi

echo "Snapshot before apply for $scope"
python3 "$helper" "${key_args[@]}" snapshot --scope "$scope" >/tmp/bkc-lab-dns-snapshot-before.json

echo "Applying desired DNS map from $map_file"
python3 "$helper" "${key_args[@]}" apply-map --map-file "$map_file" --apply >/tmp/bkc-lab-dns-apply.json

echo "Validating desired DNS map through IONOS API"
python3 "$helper" "${key_args[@]}" validate-map --map-file "$map_file" >/tmp/bkc-lab-dns-validate.json

echo "Snapshot after apply for $scope"
python3 "$helper" "${key_args[@]}" snapshot --scope "$scope" >/tmp/bkc-lab-dns-snapshot-after.json

python3 - <<'PY'
import json
from pathlib import Path

apply_result = json.loads(Path("/tmp/bkc-lab-dns-apply.json").read_text())
validate_result = json.loads(Path("/tmp/bkc-lab-dns-validate.json").read_text())

statuses = {}
for item in apply_result:
    statuses[item["status"]] = statuses.get(item["status"], 0) + 1

print(json.dumps({
    "apply_status_counts": statuses,
    "validation_status": validate_result["status"],
    "validated_records": len(validate_result["records"]),
    "artifacts": {
        "snapshot_before": "/tmp/bkc-lab-dns-snapshot-before.json",
        "apply": "/tmp/bkc-lab-dns-apply.json",
        "validate": "/tmp/bkc-lab-dns-validate.json",
        "snapshot_after": "/tmp/bkc-lab-dns-snapshot-after.json"
    }
}, indent=2, sort_keys=True))
PY

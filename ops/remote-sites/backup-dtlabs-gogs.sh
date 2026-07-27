#!/usr/bin/env bash
set -euo pipefail

remote_host="${1:-74.208.45.165}"
backup_root="${BKC_DTLABS_BACKUP_ROOT:-$HOME/Projects/auzietek/dtlabs/backups}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
local_dir="${backup_root}/${timestamp}"
remote_tmp="/tmp/bkc-dtlabs-backup-${timestamp}"

mkdir -p "${local_dir}"

ssh_opts=(
  -o BatchMode=yes
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=10
)

echo "Capturing dtlabs/Gogs metadata from ${remote_host}"

ssh "${ssh_opts[@]}" "root@${remote_host}" "mkdir -p '${remote_tmp}' && \
  date -Is > '${remote_tmp}/captured_at.txt' && \
  hostname > '${remote_tmp}/hostname.txt' && \
  docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' > '${remote_tmp}/docker-ps-a.tsv' 2>/dev/null || true && \
  docker service ls > '${remote_tmp}/docker-service-ls.txt' 2>/dev/null || true && \
  docker inspect \$(docker ps -aq --filter name=dtlab --filter name=gogs 2>/dev/null) > '${remote_tmp}/docker-inspect-dtlabs-gogs.json' 2>/dev/null || true && \
  grep -RIlE 'dtlab|gogs' /etc/nginx /svc /srv 2>/dev/null | sort -u > '${remote_tmp}/candidate-config-files.txt' || true && \
  tar -C / -czf '${remote_tmp}/dtlabs-paths.tgz' --ignore-failed-read svc/dtlabs srv/gogs 2>'${remote_tmp}/tar-stderr.txt' || true && \
  tar -C '${remote_tmp}' -czf '${remote_tmp}.tgz' ."

scp "${ssh_opts[@]}" "root@${remote_host}:${remote_tmp}.tgz" "${local_dir}/"

tar -C "${local_dir}" -xzf "${local_dir}/$(basename "${remote_tmp}").tgz"
(
  cd "${local_dir}"
  find . -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)

ssh "${ssh_opts[@]}" "root@${remote_host}" "rm -rf '${remote_tmp}' '${remote_tmp}.tgz'"

cat > "${local_dir}/MANIFEST.txt" <<MANIFEST
dtlabs/Gogs preservation capture
remote_host=${remote_host}
captured_at_utc=${timestamp}
local_dir=${local_dir}
payload_archive=${local_dir}/dtlabs-paths.tgz
metadata=docker-ps-a.tsv docker-service-ls.txt docker-inspect-dtlabs-gogs.json candidate-config-files.txt

Notes:
- Payload remains outside Git.
- Review contents before public migration.
- Retire dtlab.auzietek.com only after preservation is verified.
MANIFEST

echo "Backup captured at ${local_dir}"

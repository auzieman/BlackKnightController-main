#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  refresh-ui-canary.sh SOURCE_DIR IMAGE_TAG [CONTENT_OVERLAY_DIR]

Example:
  refresh-ui-canary.sh /home/auzieman/Projects/micro-blog astra-polish-20260731-c8bdb4b
  refresh-ui-canary.sh /home/auzieman/Projects/micro-blog astra-polish-20260731-c8bdb4b /home/auzieman/Projects/bkc-channel/payloads/packet/outputs/content

Known-good lab assumptions:
  - target ESXi manager: 10.20.0.121
  - ns1 jump host: root@192.168.1.10
  - registry bridge host: root@192.168.1.15
  - pull image name: swarm1.lab.auzietek.com:5001/micro-blog/blog-ui:TAG
  - push image name on registry host: 127.0.0.1:5001/micro-blog/blog-ui:TAG

This helper is intentionally a narrow known-good fragment for the current lab.
The BKC pipeline should eventually replace the password/transport pieces with
native bkc-ssh secret handling.
USAGE
}

if [[ $# -lt 2 ]]; then
  usage
  exit 2
fi

source_dir="$1"
image_tag="$2"
content_overlay_dir="${3:-}"

registry_pull="swarm1.lab.auzietek.com:5001"
registry_push="127.0.0.1:5001"
image_path="micro-blog/blog-ui:${image_tag}"
pull_image="${registry_pull}/${image_path}"
push_image="${registry_push}/${image_path}"
registry_bridge_host="root@192.168.1.15"
ns1_host="root@192.168.1.10"
target_user="admin-deploy"
target_manager="10.20.0.121"
target_service="micro-blog_blog-ui"
target_nodes=(10.20.0.121 10.20.0.122 10.20.0.123 10.20.0.124 10.20.0.125)
edge_url="http://swarm1.lab.auzietek.com:8091"
target_password="${BKC_ESXI_SWARM_PASSWORD:-changeme123}"

if [[ ! -d "${source_dir}" ]]; then
  echo "Source directory not found: ${source_dir}" >&2
  exit 1
fi
if [[ -n "${content_overlay_dir}" && ! -d "${content_overlay_dir}" ]]; then
  echo "Content overlay directory not found: ${content_overlay_dir}" >&2
  exit 1
fi

for required in "${source_dir}/src/ui/Dockerfile" "${source_dir}/src/ui/app.py" "${source_dir}/content"; do
  if [[ ! -e "${required}" ]]; then
    echo "Required source missing: ${required}" >&2
    exit 1
  fi
done

echo "+ build ${pull_image}"
build_dir="${source_dir}"
cleanup_build_dir=""
if [[ -n "${content_overlay_dir}" ]]; then
  cleanup_build_dir="$(mktemp -d /tmp/bkc-micro-blog-build.XXXXXX)"
  build_dir="${cleanup_build_dir}/source"
  mkdir -p "${build_dir}"
  rsync -a --delete \
    --exclude '.git' \
    --exclude '.pytest_cache' \
    --exclude 'docs/images/beta-review-20260728-bootstrap-parallax-final' \
    --exclude 'docs/images/beta-review-20260728-bootstrap-parallax' \
    --exclude 'docs/images/beta-review-20260728-live' \
    --exclude 'docs/images/beta-review-20260728-local-layout' \
    "${source_dir}/" "${build_dir}/"
  rsync -a "${content_overlay_dir}/" "${build_dir}/content/"
  trap 'rm -rf "${cleanup_build_dir}"' EXIT
  echo "+ applied private content overlay ${content_overlay_dir}"
fi

docker build -t "${pull_image}" -f "${build_dir}/src/ui/Dockerfile" "${build_dir}"

echo "+ transfer/push image through lab registry bridge"
docker save "${pull_image}" |
  ssh -o ConnectTimeout=10 "${registry_bridge_host}" \
    "docker load >/tmp/bkc-micro-blog-ui-load.log && docker tag '${pull_image}' '${push_image}' && docker push '${push_image}'"

echo "+ sync content to ESXi swarm nodes"
for host in "${target_nodes[@]}"; do
  echo "  -> ${host}"
  tar -C "${build_dir}" -cf - content |
    ssh "${ns1_host}" \
      "sshpass -p '${target_password}' ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no -o StrictHostKeyChecking=no -o UserKnownHostsFile=/tmp/bkc-esxi-swarm-known-hosts '${target_user}@${host}' 'sudo mkdir -p /srv/micro-blog && sudo tar -C /srv/micro-blog -xf - && sudo chown -R root:root /srv/micro-blog/content'"
done

echo "+ update ${target_service}"
ssh "${ns1_host}" \
  "sshpass -p '${target_password}' ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no -o StrictHostKeyChecking=no -o UserKnownHostsFile=/tmp/bkc-esxi-swarm-known-hosts '${target_user}@${target_manager}' 'sudo docker service update --image ${pull_image} ${target_service}'"

echo "+ validate core links"
for path in / /articles /principles /thinktank /aiops /business-case /friends; do
  curl -fsS "${edge_url}${path}" >/dev/null
done

echo "+ validate proof strings"
declare -A proof_strings=(
  [/]="What can Auzietek do for you"
  [/articles]="Field notes, walkthroughs, and proof-backed teaching material"
  [/principles]="Human intent remains authoritative"
  [/thinktank]="ThinkTank: Ideas with a Path Toward Useful Systems"
  [/aiops]="AIOps That Can Show Its Work"
  [/business-case]="The Business Case for BlackKnightController"
  [/friends]="Good infrastructure work is stronger"
)

for path in "${!proof_strings[@]}"; do
  needle="${proof_strings[$path]}"
  if ! curl -fsS "${edge_url}${path}" | grep -q "${needle}"; then
    echo "Missing proof string '${needle}' at ${edge_url}${path}" >&2
    exit 1
  fi
done

echo "micro-blog ESXi lab canary refresh passed: ${edge_url}/"

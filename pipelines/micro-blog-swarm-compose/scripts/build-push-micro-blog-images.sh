#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  build-push-micro-blog-images.sh SOURCE_DIR REGISTRY_HOST IMAGE_TAG

Example:
  build-push-micro-blog-images.sh /home/auzieman/Projects/micro-blog swarm1.lab.auzietek.com:5001 candidate
USAGE
}

if [[ $# -lt 3 ]]; then
  usage
  exit 2
fi

source_dir="$1"
registry_host="$2"
image_tag="$3"

if [[ ! -d "${source_dir}" ]]; then
  echo "Source directory not found: ${source_dir}" >&2
  exit 1
fi

declare -A dockerfiles=(
  [blog-api]="src/api/Dockerfile"
  [blog-worker]="src/worker/Dockerfile"
  [blog-projection]="src/projection/Dockerfile"
  [blog-ui]="src/ui/Dockerfile"
)

for service in blog-api blog-worker blog-projection blog-ui; do
  dockerfile="${dockerfiles[$service]}"
  image="${registry_host}/micro-blog/${service}:${image_tag}"
  echo "+ docker build -t ${image} -f ${dockerfile} ."
  docker build -t "${image}" -f "${source_dir}/${dockerfile}" "${source_dir}"
  echo "+ docker push ${image}"
  docker push "${image}"
done

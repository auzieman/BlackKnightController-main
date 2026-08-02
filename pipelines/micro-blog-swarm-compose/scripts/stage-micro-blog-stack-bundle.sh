#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  stage-micro-blog-stack-bundle.sh SOURCE_DIR RENDERED_STACK ENV_FILE STAGE_DIR

Example:
  stage-micro-blog-stack-bundle.sh /home/auzieman/Projects/micro-blog /tmp/micro-blog.stack.yml /secure/micro-blog.env /tmp/micro-blog-stage

Creates the minimal bundle that should be copied to the swarm manager before
`docker stack deploy`: stack YAML, .env, collector config, and an empty content
directory placeholder.
USAGE
}

if [[ $# -lt 4 ]]; then
  usage
  exit 2
fi

source_dir="$1"
rendered_stack="$2"
env_file="$3"
stage_dir="$4"

for required in "${source_dir}/collector/otel-collector-local.yaml" "${rendered_stack}" "${env_file}"; do
  if [[ ! -f "${required}" ]]; then
    echo "Required file missing: ${required}" >&2
    exit 1
  fi
done

mkdir -p "${stage_dir}/collector"
mkdir -p "${stage_dir}/content/posts"

cp "${rendered_stack}" "${stage_dir}/micro-blog.stack.yml"
cp "${env_file}" "${stage_dir}/.env"
cp "${source_dir}/collector/otel-collector-local.yaml" "${stage_dir}/collector/otel-collector-local.yaml"

echo "Staged micro-blog stack bundle in ${stage_dir}"
echo "Deploy from manager with:"
echo "  cd ${stage_dir} && docker stack deploy -c micro-blog.stack.yml micro-blog"

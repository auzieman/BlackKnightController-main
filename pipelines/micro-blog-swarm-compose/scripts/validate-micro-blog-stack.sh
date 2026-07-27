#!/usr/bin/env bash
set -euo pipefail

stack_name="${1:-micro-blog}"
ui_port="${2:-18081}"
api_port="${3:-18080}"

echo "+ docker stack services ${stack_name}"
docker stack services "${stack_name}"

echo "+ docker stack ps ${stack_name}"
docker stack ps "${stack_name}" --no-trunc

echo "+ curl API health"
curl -fsS "http://127.0.0.1:${api_port}/healthz" >/dev/null
curl -fsS "http://127.0.0.1:${api_port}/readyz" >/dev/null

echo "+ curl UI health/public"
curl -fsS "http://127.0.0.1:${ui_port}/healthz" >/dev/null
curl -fsS "http://127.0.0.1:${ui_port}/" >/dev/null

echo "micro-blog stack validation passed"

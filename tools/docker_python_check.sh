#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${BKC_TEST_PYTHON_IMAGE:-python:3.11-slim}"
docker_context="${BKC_TEST_DOCKER_CONTEXT:-default}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for this check" >&2
  exit 2
fi

default_code='from routes.beta_ui import _fabric_cards, _beta_graph_elements
cards = _fabric_cards()
elements = _beta_graph_elements({"nodes": [], "edges": []}, cards, [])
ids = [node["data"]["id"] for node in elements["nodes"]]
assert "edge:internet-cloud" in ids
assert "site:ionos" in ids
assert "host:ionos-auzietek-01" in ids
assert "host:ionos-auzietek-02" in ids
print("beta graph smoke ok: {} nodes / {} edges".format(len(elements["nodes"]), len(elements["edges"])))'

if [ "$#" -gt 0 ]; then
  python_code="$*"
else
  python_code="$default_code"
fi

docker --context "$docker_context" run --rm \
  -v "${repo_root}:/work:ro" \
  -w /work \
  "$image" \
  sh -lc 'python -m pip install --quiet --disable-pip-version-check -r requirements.txt && python - <<'"'"'PY'"'"'
'"${python_code}"'
PY'

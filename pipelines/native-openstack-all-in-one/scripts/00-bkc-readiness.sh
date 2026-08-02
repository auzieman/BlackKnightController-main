#!/usr/bin/env bash

# BKC component-script contract: do not report success until the capability,
# rather than merely the process, is ready.
bkc_wait_until() {
  local label="$1" timeout_seconds="$2"
  shift 2
  local deadline=$((SECONDS + timeout_seconds))
  until "$@"; do
    if (( SECONDS >= deadline )); then
      echo "readiness_timeout label=${label} timeout=${timeout_seconds}s command=$*" >&2
      return 1
    fi
    sleep 2
  done
  echo "readiness_ready label=${label}"
}

bkc_wait_service() {
  local service="$1" timeout_seconds="${2:-180}"
  bkc_wait_until "service:${service}" "$timeout_seconds" systemctl is-active --quiet "$service"
}

bkc_wait_http() {
  local name="$1" url="$2" timeout_seconds="${3:-180}"
  bkc_wait_until "http:${name}" "$timeout_seconds" curl -kfsS --max-time 10 "$url" -o /dev/null
}

bkc_component_trap() {
  local component="$1"
  trap 'rc=$?; echo "component_failed name='"$component"' line=${LINENO} rc=${rc}" >&2' ERR
}

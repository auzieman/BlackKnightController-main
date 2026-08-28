#!/usr/bin/env bash
set -euo pipefail

HDD_ID="${1:-desktop-main-20260828-r8}"
VIEW="${2:-dashboard}"
LAB_HOST="${AUZIX_LAB_HOST:-lab-ai-worker}"
BUILD_ROOT="${AUZIX_BUILD_ROOT:-/var/lib/auzix-build}"
RUN_DIR="${BUILD_ROOT}/hdd-runs/${HDD_ID}"
IMAGE_DIR="${BUILD_ROOT}/hdd-images/${HDD_ID}"
LOG="${BUILD_ROOT}/receipts/hdd-${HDD_ID}-launch.log"
RECEIPT="${BUILD_ROOT}/receipts/release-hdd-${HDD_ID}.receipt"

case "${VIEW}" in
  log)
    exec ssh "${LAB_HOST}" tail -n 40 -F "${LOG}"
    ;;
  progress)
    exec ssh "${LAB_HOST}" "tail -n 80 -F '${LOG}'" \
      | grep --line-buffered -E 'INSTALL_STAGE|INSTALL |REQUEST|TIER|DONE|MISSING|FATAL|PASS|FAIL'
    ;;
  health)
    exec ssh -t "${LAB_HOST}" 'command -v glances >/dev/null && exec glances; exec htop'
    ;;
  dashboard)
    while :; do
      clear
      ssh "${LAB_HOST}" bash -s -- "${HDD_ID}" "${RUN_DIR}" "${IMAGE_DIR}" "${LOG}" "${RECEIPT}" <<'REMOTE'
set +e
hdd_id="$1"; run_dir="$2"; image_dir="$3"; log="$4"; receipt="$5"
printf 'AUZiX HDD  %s  %s\n\n' "${hdd_id}" "$(date '+%F %T %Z')"
if pgrep -af '[b]uild-validated-hdd.sh build' >/dev/null; then
  printf 'STATE     RUNNING\n'
elif [[ -s "${receipt}" ]] && grep -q '^status=built$' "${receipt}"; then
  printf 'STATE     BUILT\n'
else
  printf 'STATE     STOPPED WITHOUT BUILD RECEIPT\n'
fi
pgrep -af '[b]uild-validated-hdd.sh build' || true
printf '\nSTORAGE\n'
du -sh "${run_dir}" "${image_dir}" 2>/dev/null || true
printf '\nLOOP/MOUNTS\n'
losetup -a | grep -F "${hdd_id}" || printf 'none\n'
mount | grep -F "${hdd_id}" || true
printf '\nLAST ACTIVITY\n'
tail -n 12 "${log}" 2>/dev/null || printf 'log not created\n'
REMOTE
      sleep 2
    done
    ;;
  *)
    printf 'usage: %s [hdd-id] [dashboard|log|progress|health]\n' "$0" >&2
    exit 2
    ;;
esac

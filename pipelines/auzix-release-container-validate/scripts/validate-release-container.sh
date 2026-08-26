#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-preflight}"
RELEASE_ID="${AUZIX_RELEASE_ID:-trixie-consolidated-20260826-r1}"
VALIDATION_ID="${AUZIX_VALIDATION_ID:-container-proof-r1}"
IMAGE="${AUZIX_VALIDATION_IMAGE:-auzix/release-validation:trixie-r1}"
RELEASE_ROOT="${AUZIX_RELEASE_ROOT:-/var/lib/auzix-build/releases/${RELEASE_ID}}"
REPO="${RELEASE_ROOT}/repo"
WORK="${RELEASE_ROOT}/validation/${VALIDATION_ID}"
ROOT="${WORK}/root"
RECEIPT="${AUZIX_RECEIPT_DIR:-/var/lib/auzix-build/receipts}/release-container-${VALIDATION_ID}.receipt"

log() { printf '[auzix-release-container] %s\n' "$*"; }
fail() { log "FAIL: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }

preflight() {
  need docker; need python3; need jq; need tar; need sha256sum; need chroot
  [[ -s "${RELEASE_ROOT}/release-manifest.json" && -s "${REPO}/index.json" ]] || fail "frozen release is missing"
  (cd "${RELEASE_ROOT}" && sha256sum -c SHA256SUMS)
  [[ ! -e "${WORK}" ]] || fail "validation output already exists: ${WORK}"
  local indexed archives
  indexed="$(jq '.packages | length' "${REPO}/index.json")"
  archives="$(find "${REPO}/packages" -maxdepth 1 -type f -name '*.auzix.tar.gz' | wc -l | tr -d ' ')"
  [[ "${indexed}" == "${archives}" ]] || fail "index/archive mismatch ${indexed}/${archives}"
  log "preflight release=${RELEASE_ID} packages=${indexed} image=${IMAGE}"
}

materialize() {
  preflight
  mkdir -p "${ROOT}" "$(dirname "${RECEIPT}")"
  python3 - "${REPO}/index.json" "${REPO}/packages" "${WORK}/install-order.txt" <<'PY'
import json, sys
from pathlib import Path
index, package_dir, order_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
items = json.loads(index.read_text()).get("packages", [])
by_name = {str(x["name"]).casefold(): x for x in items}
missing = sorted({str(d) for x in items for d in (x.get("depends") or []) if str(d).casefold() not in by_name})
if missing:
    raise SystemExit("missing repository dependencies: " + ", ".join(missing[:80]))
remaining = set(by_name); complete = set(); order = []; ordered_items = []
while remaining:
    ready = sorted(k for k in remaining if all(str(d).casefold() in complete or str(d).casefold() not in remaining for d in (by_name[k].get("depends") or [])))
    if not ready:  # Preserve deterministic SCC order; payloads precede all hook execution.
        ready = [min(remaining)]
    for key in ready:
        order.append(str(by_name[key]["package"])); ordered_items.append(by_name[key]); complete.add(key); remaining.remove(key)
order_path.write_text("".join(x + "\n" for x in order))
(order_path.parent / "post-install-hooks.txt").write_text("".join(
    str((item.get("hooks") or {}).get("post_install") or "").strip() + "\n"
    for item in ordered_items
    if str((item.get("hooks") or {}).get("post_install") or "").strip()
))
PY
  while IFS= read -r archive; do
    [[ -n "${archive}" ]] || continue
    tar --numeric-owner -xzf "${REPO}/packages/${archive}" -C "${ROOT}"
  done <"${WORK}/install-order.txt"
  [[ -x "${ROOT}/Programs/BusyBox/current/Commands/busybox" ]] || fail "fresh root lacks BusyBox"
  mkdir -p "${ROOT}/System/State/packages" "${ROOT}/Work/Temp" "${ROOT}/Users/auzix"
  jq '{format:"auzix-installed-v1",installed:[.packages[]|{name,version,kind,package,sha256,depends:(.depends//[]),commands:(.commands//[]),desktop_entries:(.desktop_entries//[]),hooks:(.hooks//{})}]}' \
    "${REPO}/index.json" >"${ROOT}/System/State/packages/installed.json"
  chown 1000:1000 "${ROOT}/Users/auzix" 2>/dev/null || true
  while IFS= read -r hook; do
    [[ -n "${hook}" ]] || continue
    read -r -a hook_argv <<<"${hook}"
    [[ "${hook_argv[0]}" == /Programs/* ]] || fail "refusing post-install hook outside /Programs: ${hook}"
    chroot "${ROOT}" "${hook_argv[@]}"
  done <"${WORK}/post-install-hooks.txt"
  log "materialized root=${ROOT}"
}

import_image() {
  [[ -x "${ROOT}/Programs/BusyBox/current/Commands/busybox" ]] || fail "materialized root missing"
  tar --numeric-owner -C "${ROOT}" -cf - . | docker import \
    --change 'WORKDIR /Work' \
    --change 'ENV HOME=/Users/root' \
    --change 'ENV TERM=xterm-256color' \
    --change 'ENV PATH=/System/Compatibility/bin:/System/Compatibility/sbin:/Programs/BusyBox/current/Commands:/Programs/Glances/current/Commands:/Programs/Htop/current/Commands:/Programs/Flatpak/current/Commands' \
    --change 'ENV SSL_CERT_FILE=/System/Compatibility/etc/ssl/certs/ca-certificates.crt' \
    --change 'CMD ["/Programs/BusyBox/current/Commands/busybox","sh"]' \
    - "${IMAGE}"
  log "imported image=${IMAGE}"
}

probe() {
  docker image inspect "${IMAGE}" >/dev/null || fail "validation image missing: ${IMAGE}"
  local report="${WORK}/validation-report.json" failures=0
  mkdir -p "${WORK}" "$(dirname "${RECEIPT}")"
  run_probe() {
    local label="$1"; shift
    if docker run --rm "$@"; then log "PASS ${label}"; else log "FAIL ${label}"; failures=$((failures + 1)); fi
  }
  run_probe busybox "${IMAGE}" /Programs/BusyBox/current/Commands/busybox true
  run_probe core-libc "${IMAGE}" /Programs/BusyBox/current/Commands/busybox test -x /System/Libraries/Runtime/glibc/libc.so.6
  run_probe no-alternate-libc "${IMAGE}" /Programs/BusyBox/current/Commands/busybox sh -c '! test -e /Programs/Libc6/current'
  run_probe normal-user --user 1000:1000 -e HOME=/Users/auzix "${IMAGE}" /Programs/BusyBox/current/Commands/busybox test -d /Users/auzix
  for spec in 'glances:/Programs/Glances/current/Commands/glances:--version' 'htop:/Programs/Htop/current/Commands/htop:--version' 'flatpak:/Programs/Flatpak/current/Commands/flatpak:--version'; do
    IFS=: read -r label command arg <<<"${spec}"
    if docker run --rm "${IMAGE}" /Programs/BusyBox/current/Commands/busybox test -x "${command}"; then
      run_probe "${label}" "${IMAGE}" "${command}" "${arg}"
    else
      log "WARN ${label} not selected in this release"
    fi
  done
  jq -n --arg release_id "${RELEASE_ID}" --arg validation_id "${VALIDATION_ID}" --arg image "${IMAGE}" --argjson failures "${failures}" \
    '{format:"auzix-release-container-validation-v1",release_id:$release_id,validation_id:$validation_id,image:$image,failures:$failures,status:(if $failures==0 then "pass" else "fail" end)}' >"${report}"
  [[ "${failures}" == 0 ]] || fail "${failures} validation probes failed"
  printf 'format=auzix-release-container-v1\nrelease_id=%s\nvalidation_id=%s\nimage=%s\nstatus=pass\nreport=%s\ncompleted_at=%s\n' \
    "${RELEASE_ID}" "${VALIDATION_ID}" "${IMAGE}" "${report}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${RECEIPT}"
  log "validation passed receipt=${RECEIPT}"
}

case "${MODE}" in
  preflight) preflight ;;
  materialize) materialize ;;
  import) import_image ;;
  probe) probe ;;
  *) fail "unknown mode: ${MODE}" ;;
esac

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
SELECTION_FILE="${AUZIX_SELECTION_FILE:-}"
SELECTION_JSON="${AUZIX_SELECTION_JSON:-}"
SELECTION_B64="${AUZIX_SELECTION_B64:-}"

log() { printf '[auzix-release-container] %s\n' "$*"; }
fail() { log "FAIL: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"; }

write_selection() {
  mkdir -p "${WORK}"
  if [[ -n "${SELECTION_FILE}" ]]; then
    [[ -s "${SELECTION_FILE}" ]] || fail "selection file missing: ${SELECTION_FILE}"
    cp "${SELECTION_FILE}" "${WORK}/selection.json"
  elif [[ -n "${SELECTION_B64}" ]]; then
    printf '%s' "${SELECTION_B64}" | base64 -d >"${WORK}/selection.json"
  elif [[ -n "${SELECTION_JSON}" ]]; then
    printf '%s\n' "${SELECTION_JSON}" >"${WORK}/selection.json"
  else
    fail "an explicit validation selection is required"
  fi
  jq -e '.format == "auzix-validation-selection-v1" and (.roots | length > 0)' "${WORK}/selection.json" >/dev/null \
    || fail "invalid validation selection"
}

plan_selection() {
  write_selection
  python3 - "${REPO}/index.json" "${WORK}/selection.json" "${WORK}" <<'PY'
import json, sys
from pathlib import Path
index_path, selection_path, work = map(Path, sys.argv[1:])
items = json.loads(index_path.read_text()).get("packages", [])
roots = json.loads(selection_path.read_text()).get("roots", [])
by_name = {str(x["name"]).casefold(): x for x in items}
seen, visiting, order, missing, cycles = set(), set(), [], [], []
def visit(name, parent="selection"):
    key = str(name).casefold()
    if key in seen: return
    if key in visiting:
        cycles.append((str(name), parent)); return
    item = by_name.get(key)
    if item is None:
        missing.append((str(name), parent)); return
    visiting.add(key)
    for dep in item.get("depends") or []:
        visit(dep, str(item["name"]))
    visiting.remove(key); seen.add(key); order.append(item)
for root in roots: visit(root)
missing = sorted(set(missing), key=lambda x: (x[0].casefold(), x[1].casefold()))
missing_names = sorted({name for name,_ in missing}, key=str.casefold)
(work / "missing-dependencies.tsv").write_text("".join(f"{n}\t{p}\n" for n,p in missing))
(work / "dependency-cycles.tsv").write_text("".join(f"{n}\t{p}\n" for n,p in cycles))
(work / "install-order.txt").write_text("".join(str(x["package"]) + "\n" for x in order))
(work / "selected-packages.json").write_text(json.dumps({
    "format":"auzix-selected-closure-v1", "roots":roots,
    "package_count":len(order), "missing_count":len(missing_names),
    "missing_edge_count":len(missing),
    "packages":order
}, indent=2) + "\n")
(work / "post-install-hooks.txt").write_text("".join(
    str((x.get("hooks") or {}).get("post_install") or "").strip() + "\n"
    for x in order if str((x.get("hooks") or {}).get("post_install") or "").strip()
))
if missing:
    print(f"selected closure is incomplete: {len(missing_names)} missing packages across {len(missing)} dependency edges", file=sys.stderr)
    for name,parent in missing[:80]: print(f"MISSING {name} required_by={parent}", file=sys.stderr)
    raise SystemExit(42)
print(f"selected closure packages={len(order)} roots={len(roots)} cycles={len(cycles)}")
PY
}

preflight() {
  need docker; need python3; need jq; need tar; need sha256sum
  [[ -s "${RELEASE_ROOT}/release-manifest.json" && -s "${REPO}/index.json" ]] || fail "frozen release is missing"
  (cd "${RELEASE_ROOT}" && sha256sum -c SHA256SUMS)
  [[ ! -e "${ROOT}" ]] || fail "validation root already exists: ${ROOT}"
  local indexed archives
  indexed="$(jq '.packages | length' "${REPO}/index.json")"
  archives="$(find "${REPO}/packages" -maxdepth 1 -type f -name '*.auzix.tar.gz' | wc -l | tr -d ' ')"
  [[ "${indexed}" == "${archives}" ]] || fail "index/archive mismatch ${indexed}/${archives}"
  plan_selection
  log "preflight release=${RELEASE_ID} catalog_packages=${indexed} selected_packages=$(jq -r .package_count "${WORK}/selected-packages.json") image=${IMAGE}"
}

materialize() {
  [[ -s "${WORK}/selected-packages.json" && -s "${WORK}/install-order.txt" ]] || fail "preflight selection receipt is missing"
  [[ "$(jq -r .missing_count "${WORK}/selected-packages.json")" == 0 ]] || fail "selected closure is incomplete"
  [[ ! -e "${ROOT}" ]] || fail "validation root already exists: ${ROOT}"
  mkdir -p "${ROOT}" "$(dirname "${RECEIPT}")"
  while IFS= read -r archive; do
    [[ -n "${archive}" ]] || continue
    tar --numeric-owner -xzf "${REPO}/packages/${archive}" -C "${ROOT}"
  done <"${WORK}/install-order.txt"
  mkdir -p "${ROOT}/System/State/packages" "${ROOT}/Work/Temp" "${ROOT}/Users/auzix"
  jq '{format:"auzix-installed-v1",installed:[.packages[]|{name,version,kind,package,sha256,depends:(.depends//[]),commands:(.commands//[]),desktop_entries:(.desktop_entries//[]),hooks:(.hooks//{})}]}' \
    "${WORK}/selected-packages.json" >"${ROOT}/System/State/packages/installed.json"
  chown 1000:1000 "${ROOT}/Users/auzix" 2>/dev/null || true
  log "materialized root=${ROOT}; runtime validation deferred until after container import"
}

validate_desktop_launchers() {
  local image_root="${WORK}/image-root"
  local inspect_container="auzix-release-inspect-${VALIDATION_ID//[^a-zA-Z0-9_.-]/-}"
  rm -rf "${image_root}"
  mkdir -p "${image_root}"
  docker rm -f "${inspect_container}" >/dev/null 2>&1 || true
  docker create --name "${inspect_container}" "${IMAGE}" >/dev/null
  docker export "${inspect_container}" | tar --numeric-owner -xf - -C "${image_root}"
  docker rm "${inspect_container}" >/dev/null
  python3 - "${WORK}/selected-packages.json" "${image_root}" "${WORK}/desktop-launchers.json" <<'PY'
import json, re, shlex, sys
from pathlib import Path
selected, root, report = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
items = json.loads(selected.read_text()).get("packages", [])
declared = sorted({str(p) for x in items for p in (x.get("desktop_entries") or [])})
rows, failures = [], []
for rel in declared:
    path = root / rel.lstrip("/")
    row = {"path": rel, "exists": path.is_file(), "exec": None, "resolved": False}
    if not path.is_file():
        failures.append(f"missing desktop file {rel}"); rows.append(row); continue
    match = re.search(r"(?m)^Exec=(.+)$", path.read_text(errors="replace"))
    if not match:
        failures.append(f"desktop file lacks Exec {rel}"); rows.append(row); continue
    command = shlex.split(re.sub(r"%[fFuUdDnNickvm]", "", match.group(1).strip()))[0]
    row["exec"] = command
    candidates = [root / command.lstrip("/")] if command.startswith("/") else []
    candidates += list(root.glob(f"Programs/*/current/Commands/{command}"))
    candidates += list(root.glob(f"Programs/*/*/Commands/{command}"))
    row["resolved"] = any(p.exists() for p in candidates)
    if not row["resolved"]: failures.append(f"unresolved desktop Exec {rel}: {command}")
    rows.append(row)
report.write_text(json.dumps({"format":"auzix-desktop-launcher-proof-v1","entries":rows,"failures":failures},indent=2)+"\n")
if failures:
    raise SystemExit("; ".join(failures[:30]))
PY
}

import_image() {
  tar --numeric-owner -C "${ROOT}" -cf - . | docker import \
    --change 'WORKDIR /Work' \
    --change 'ENV HOME=/Users/root' \
    --change 'ENV TERM=xterm-256color' \
    --change 'ENV PATH=/System/Compatibility/bin:/System/Compatibility/sbin:/Programs/Glances/current/Commands:/Programs/Htop/current/Commands:/Programs/Flatpak/current/Commands' \
    --change 'ENV SSL_CERT_FILE=/System/Compatibility/etc/ssl/certs/ca-certificates.crt' \
    --change 'CMD ["/System/Compatibility/bin/busybox","sh"]' \
    - "${IMAGE}"
  local assembly_container="auzix-release-assembly-${VALIDATION_ID//[^a-zA-Z0-9_.-]/-}"
  docker rm -f "${assembly_container}" >/dev/null 2>&1 || true
  docker run -d --name "${assembly_container}" "${IMAGE}" /System/Compatibility/bin/busybox sh -c \
    'while :; do sleep 3600; done' >/dev/null
  while IFS= read -r hook; do
    [[ -n "${hook}" ]] || continue
    read -r -a hook_argv <<<"${hook}"
    [[ "${hook_argv[0]}" == /Programs/* ]] || fail "refusing post-install hook outside /Programs: ${hook}"
    docker exec "${assembly_container}" "${hook_argv[@]}"
  done <"${WORK}/post-install-hooks.txt"
  docker commit "${assembly_container}" "${IMAGE}" >/dev/null
  docker rm -f "${assembly_container}" >/dev/null
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
  run_probe busybox "${IMAGE}" /System/Compatibility/bin/busybox true
  run_probe core-libc "${IMAGE}" /System/Compatibility/bin/busybox test -x /System/Libraries/Runtime/glibc/libc.so.6
  run_probe no-alternate-libc "${IMAGE}" /System/Compatibility/bin/busybox sh -c '! test -e /Programs/Libc6/current'
  run_probe normal-user --user 1000:1000 -e HOME=/Users/auzix "${IMAGE}" /System/Compatibility/bin/busybox test -d /Users/auzix
  run_probe ncurses-terminfo "${IMAGE}" /System/Compatibility/bin/busybox sh -c 'test -n "$(find /Programs/NcursesBase /Programs/NcursesTerm -type f -name xterm-256color -print -quit 2>/dev/null)"'
  run_probe python-ssl-curses "${IMAGE}" /System/Compatibility/bin/busybox sh -c 'p=$(command -v python3 || command -v python3.13); test -n "$p"; "$p" -c "import curses,ssl,sqlite3; print(ssl.OPENSSL_VERSION)"'
  for spec in 'glances:/Programs/Glances/current/Commands/glances:--version' 'htop:/Programs/Htop/current/Commands/htop:--version'; do
    IFS=: read -r label command arg <<<"${spec}"
    run_probe "${label}-normal-user" --user 1000:1000 -e HOME=/Users/auzix -e TERM=xterm-256color "${IMAGE}" "${command}" "${arg}"
  done
  run_probe libreoffice-headless-convert --user 1000:1000 -e HOME=/Users/auzix "${IMAGE}" /System/Compatibility/bin/busybox sh -c \
    'mkdir -p /Work/lo-proof; printf "AUZiX conversion proof\n" >/Work/lo-proof/input.txt; loffice --headless --convert-to pdf --outdir /Work/lo-proof /Work/lo-proof/input.txt; test -s /Work/lo-proof/input.pdf'
  run_probe desktop-launcher-contract "${IMAGE}" /System/Compatibility/bin/busybox test -s /System/State/packages/installed.json
  if validate_desktop_launchers; then log "PASS desktop-launcher-resolution"; else log "FAIL desktop-launcher-resolution"; failures=$((failures + 1)); fi
  jq -n --arg release_id "${RELEASE_ID}" --arg validation_id "${VALIDATION_ID}" --arg image "${IMAGE}" --argjson failures "${failures}" \
    '{format:"auzix-release-container-validation-v1",release_id:$release_id,validation_id:$validation_id,image:$image,failures:$failures,status:(if $failures==0 then "pass" else "fail" end)}' >"${report}"
  [[ "${failures}" == 0 ]] || fail "${failures} validation probes failed"
  local container="auzix-release-validation-${VALIDATION_ID//[^a-zA-Z0-9_.-]/-}"
  docker rm -f "${container}" >/dev/null 2>&1 || true
  docker run -d --name "${container}" "${IMAGE}" /System/Compatibility/bin/busybox sh -c \
    'while :; do sleep 3600; done' >/dev/null
  printf 'format=auzix-release-container-v1\nrelease_id=%s\nvalidation_id=%s\nimage=%s\nstatus=pass\nreport=%s\ncompleted_at=%s\n' \
    "${RELEASE_ID}" "${VALIDATION_ID}" "${IMAGE}" "${report}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${RECEIPT}"
  printf 'container=%s\n' "${container}" >>"${RECEIPT}"
  log "validation passed receipt=${RECEIPT}"
}

case "${MODE}" in
  preflight) preflight ;;
  materialize) materialize ;;
  import) import_image ;;
  probe) probe ;;
  *) fail "unknown mode: ${MODE}" ;;
esac

#!/usr/bin/env bash
set -euo pipefail

PROOF_ID="${1:?usage: prove-hdd-bootstrap-root.sh PROOF_ID [COUNT]}"
LIMIT="${2:-33}"
BUILD_ROOT="${AUZIX_BUILD_ROOT:-/var/lib/auzix-build}"
PROOF_ROOT="${AUZIX_TRANSACTION_DIR:-${BUILD_ROOT}/hdd-transaction-proofs/${PROOF_ID}}"
TARGET="${AUZIX_TARGET_ROOT:-${PROOF_ROOT}/root}"
PLAN="${PROOF_ROOT}/install-plan.json"
JOURNAL="${PROOF_ROOT}/unpack.tsv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

R5_REPO="${AUZIX_R5_REPO:-${BUILD_ROOT}/releases/trixie-consolidated-20260828-r5/repo}"
COMPOSITE_REPO="${AUZIX_COMPOSITE_REPO:-${BUILD_ROOT}/native-rebase-runs/trixie-base-20260824-r3/src/artifacts/auzix/repo-trixie-composite-proof}"
NATIVE_REPO="${AUZIX_NATIVE_REPO:-${BUILD_ROOT}/runs/strict-r8-busybox-xorg-dns-20260823T174356Z/src/artifacts/auzix/repo}"
WRITER_SPOOL="${AUZIX_WRITER_SPOOL:-${BUILD_ROOT}/package-finalizations/libreoffice-writer-finalize-20260828-r2/spool}"
PROFILE="${AUZIX_PROFILE_SOURCE:-/srv/nfs/swarm/blackknightcontroller/runtime/pipelines/auzix-release-hdd-build-deploy/profiles/desktop-main.packages}"
SOURCE_ROOT="${AUZIX_SOURCE_ROOT:-${BUILD_ROOT}/hdd-runs/desktop-main-20260828-r7/source}"
SCAFFOLD="${SOURCE_ROOT}/scripts/scaffold-auzix-strict-root.sh"

[[ "${PROOF_ID}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "invalid proof ID" >&2; exit 2; }
[[ "${LIMIT}" =~ ^[0-9]+$ ]] && (( LIMIT > 0 )) || { echo "invalid package count" >&2; exit 2; }
if [[ -z "${AUZIX_TRANSACTION_DIR:-}" ]]; then
  [[ ! -e "${PROOF_ROOT}" ]] || { echo "proof already exists: ${PROOF_ROOT}" >&2; exit 1; }
else
  [[ -d "${PROOF_ROOT}" && ! -e "${PLAN}" && ! -e "${PROOF_ROOT}/receipt" ]] || {
    echo "transaction directory is not fresh: ${PROOF_ROOT}" >&2; exit 1;
  }
fi
mkdir -p "${TARGET}/System/PackageDB" "${TARGET}/System/State/packages" "${TARGET}/Programs" "${TARGET}/Work/Temp"
[[ -x "${SCAFFOLD}" ]] || { echo "immutable scaffold script missing: ${SCAFFOLD}" >&2; exit 1; }
AUZIX_LINK_MODE=strict "${SCAFFOLD}" "${TARGET}" >"${PROOF_ROOT}/scaffold.log" 2>&1

"${SCRIPT_DIR}/resolve-hdd-install-plan.py" \
  --r5 "${R5_REPO}" --composite "${COMPOSITE_REPO}" --native "${NATIVE_REPO}" \
  --writer "${WRITER_SPOOL}" --profile "${PROFILE}" --output "${PLAN}"
printf 'sequence\tname\tversion\tsha256\tarchive\n' >"${JOURNAL}"

archive_for() {
  case "$1" in
    r5) printf '%s/packages/%s\n' "${R5_REPO}" "$2" ;;
    composite) printf '%s/packages/%s\n' "${COMPOSITE_REPO}" "$2" ;;
    native) printf '%s/packages/%s\n' "${NATIVE_REPO}" "$2" ;;
    writer) printf '%s/packages/%s\n' "${WRITER_SPOOL}" "$2" ;;
    *) echo "unknown source: $1" >&2; return 1 ;;
  esac
}

while IFS=$'\t' read -r sequence name version source package expected; do
  archive="$(archive_for "${source}" "${package}")"
  [[ -s "${archive}" ]] || { echo "archive missing: ${archive}" >&2; exit 1; }
  actual="$(sha256sum "${archive}" | awk '{print $1}')"
  [[ "${actual}" == "${expected}" ]] || { echo "hash mismatch: ${name}" >&2; exit 1; }
  mapfile -t archive_receipts < <(tar -tzf "${archive}" | grep -E '^\.?/?System/PackageDB/[^/]+[.]json$' || true)
  tar -xzpf "${archive}" -C "${TARGET}"
  receipt_name=""
  for receipt in "${archive_receipts[@]}"; do
    receipt="${TARGET}/${receipt#./}"
    [[ -s "${receipt}" ]] || continue
    candidate="$(jq -r --arg wanted "${name}" 'select((.name|ascii_downcase)==($wanted|ascii_downcase)) | .name' "${receipt}")"
    if [[ -n "${candidate}" ]]; then receipt_name="${candidate}"; break; fi
  done
  [[ -n "${receipt_name}" ]] || { echo "package receipt missing after unpack: ${name}" >&2; exit 1; }
  printf '%s\t%s\t%s\t%s\t%s\n' "${sequence}" "${name}" "${version}" "${actual}" "${package}" >>"${JOURNAL}"
done < <(jq -r --argjson limit "${LIMIT}" '.packages[:$limit][] | [.sequence,.name,.version,.source,.package,.sha256] | @tsv' "${PLAN}")

installed="$(($(wc -l <"${JOURNAL}") - 1))"
[[ "${installed}" -eq "${LIMIT}" ]] || { echo "journal count mismatch: ${installed} != ${LIMIT}" >&2; exit 1; }
chroot "${TARGET}" /Programs/BusyBox/1.36.1/Commands/busybox sh -c \
  'test -x /Programs/BusyBox/current/Commands/busybox && /Programs/BusyBox/current/Commands/busybox true'
chroot "${TARGET}" /Programs/BusyBox/1.36.1/Commands/busybox sh -c \
  '/Programs/AuzixPackageTools/current/Commands/jq --version'

python3 - "${TARGET}" "${PROOF_ROOT}/content-manifest.sha256" <<'PY'
import hashlib, os, stat, sys
from pathlib import Path
root, output = Path(sys.argv[1]), Path(sys.argv[2])
digest = hashlib.sha256()
with output.open("w") as stream:
    for path in sorted(root.rglob("*"), key=lambda p: str(p.relative_to(root))):
        relative = str(path.relative_to(root))
        mode = path.lstat().st_mode
        if stat.S_ISREG(mode):
            content = hashlib.sha256(path.read_bytes()).hexdigest()
            record = f"file\t{mode & 0o7777:04o}\t{content}\t{relative}\n"
        elif stat.S_ISLNK(mode):
            record = f"link\t{mode & 0o7777:04o}\t{os.readlink(path)}\t{relative}\n"
        elif stat.S_ISDIR(mode):
            record = f"dir\t{mode & 0o7777:04o}\t-\t{relative}\n"
        else:
            record = f"other\t{mode:o}\t-\t{relative}\n"
        stream.write(record); digest.update(record.encode())
print(digest.hexdigest())
PY
content_sha256="$(sha256sum "${PROOF_ROOT}/content-manifest.sha256" | awk '{print $1}')"

cat >"${PROOF_ROOT}/receipt" <<EOF
format=auzix-hdd-bootstrap-proof-v1
proof_id=${PROOF_ID}
install_plan_sha256=$(jq -r .plan_content_sha256 "${PLAN}")
unpacked_packages=${installed}
first_package=$(jq -r '.packages[0] | "\(.name) \(.version)"' "${PLAN}")
last_package=$(jq -r --argjson limit "${LIMIT}" '.packages[$limit-1] | "\(.name) \(.version)"' "${PLAN}")
busybox_chroot=pass
package_tools_jq_chroot=pass
strict_scaffold=pass
content_manifest_sha256=${content_sha256}
status=passed
EOF
cat "${PROOF_ROOT}/receipt"

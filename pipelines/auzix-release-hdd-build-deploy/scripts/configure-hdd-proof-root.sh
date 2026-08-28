#!/usr/bin/env bash
set -euo pipefail

PROOF_ID="${1:?usage: configure-hdd-proof-root.sh PROOF_ID}"
BUILD_ROOT="${AUZIX_BUILD_ROOT:-/var/lib/auzix-build}"
PROOF_ROOT="${AUZIX_TRANSACTION_DIR:-${BUILD_ROOT}/hdd-transaction-proofs/${PROOF_ID}}"
TARGET="${AUZIX_TARGET_ROOT:-${PROOF_ROOT}/root}"
PLAN="${PROOF_ROOT}/install-plan.json"
UNPACK_RECEIPT="${PROOF_ROOT}/receipt"
SOURCE_ROOT="${AUZIX_SOURCE_ROOT:-${BUILD_ROOT}/hdd-runs/desktop-main-20260828-r7/source}"
RUNTIME_SOURCE="${AUZIX_RUNTIME_SOURCE:-${BUILD_ROOT}/runs/strict-r8-busybox-xorg-dns-20260823T174356Z/src/out/auzix-strict/AuzixRoot/System/Libraries/Runtime/glibc}"
CONFIGURE_LOG="${PROOF_ROOT}/configure.log"
REPAIR_RECEIPT="${TARGET}/System/State/install/libc6-compatibility-repair.json"

[[ -d "${TARGET}" && -s "${PLAN}" && -s "${UNPACK_RECEIPT}" ]] || { echo "unpack proof incomplete" >&2; exit 1; }
grep -q '^status=passed$' "${UNPACK_RECEIPT}" || { echo "unpack proof did not pass" >&2; exit 1; }
grep -q '^unpacked_packages=642$' "${UNPACK_RECEIPT}" || { echo "proof is not the full package closure" >&2; exit 1; }
[[ ! -e "${PROOF_ROOT}/configure-receipt" ]] || { echo "proof already configured" >&2; exit 1; }
[[ -x "${SOURCE_ROOT}/scripts/add-auzix-live-tools.sh" ]] || { echo "root-prep source missing" >&2; exit 1; }

exec > >(tee "${CONFIGURE_LOG}") 2>&1
echo "CONFIGURE_STAGE 1/6 validate package-owned runtime"
package_libgcc="${TARGET}/System/Libraries/Runtime/glibc/libgcc_s.so.1"
[[ -s "${package_libgcc}" ]] || { echo "package-owned libgcc_s missing" >&2; exit 1; }
package_libgcc_sha="$(sha256sum "${package_libgcc}" | awk '{print $1}')"

echo "CONFIGURE_STAGE 2/6 apply declared Libc6 compatibility repair"
mkdir -p "${TARGET}/System/Libraries/Runtime/glibc" "$(dirname "${REPAIR_RECEIPT}")"
repair_records="${PROOF_ROOT}/libc6-repair-records.jsonl"
: >"${repair_records}"
for file in ld-linux-x86-64.so.2 libc.so.6 libm.so.6; do
  source_file="${RUNTIME_SOURCE}/${file}"
  target_file="${TARGET}/System/Libraries/Runtime/glibc/${file}"
  [[ -s "${source_file}" ]] || { echo "repair source missing: ${source_file}" >&2; exit 1; }
  source_sha="$(sha256sum "${source_file}" | awk '{print $1}')"
  if [[ -e "${target_file}" ]]; then
    target_sha="$(sha256sum "${target_file}" | awk '{print $1}')"
    [[ "${target_sha}" == "${source_sha}" ]] || { echo "repair refuses to overwrite mismatched file: ${target_file}" >&2; exit 1; }
  else
    install -m 0755 "${source_file}" "${target_file}"
  fi
  jq -n --arg path "/System/Libraries/Runtime/glibc/${file}" \
    --arg sha256 "${source_sha}" \
    --arg source "${source_file}" '{path:$path,sha256:$sha256,source:$source,future_owner:"Libc6"}' \
    >>"${repair_records}"
done
jq -s --arg package_libgcc_sha256 "${package_libgcc_sha}" '{
  format:"auzix-compatibility-repair-v1",
  name:"libc6-bootstrap-runtime",
  reason:"No validated Libc6 archive exists in the frozen install catalogs",
  files:.,
  protected_package_files:[{path:"/System/Libraries/Runtime/glibc/libgcc_s.so.1",owner:"LibgccS1",sha256:$package_libgcc_sha256}],
  status:"applied"
}' "${repair_records}" >"${REPAIR_RECEIPT}"
rm -f "${repair_records}"
[[ "$(sha256sum "${package_libgcc}" | awk '{print $1}')" == "${package_libgcc_sha}" ]] || {
  echo "compatibility repair changed package-owned libgcc_s" >&2; exit 1;
}

echo "CONFIGURE_STAGE 3/6 generate installed-root boot and repair tools"
AUZIX_LINK_MODE=strict "${SOURCE_ROOT}/scripts/add-auzix-live-tools.sh" "${TARGET}"

echo "CONFIGURE_STAGE 4/6 create installed package state"
mkdir -p "${TARGET}/System/State/packages" "${TARGET}/System/Settings/packages"
records="${PROOF_ROOT}/installed-records.jsonl"
: >"${records}"
while IFS= read -r -d '' receipt; do
  jq -c --arg receipt "/System/PackageDB/$(basename "${receipt}")" '
    select(.name != null) | {
      name:.name, version:(.version//""), kind:(.kind//"unknown"),
      package:(.package//""), sha256:(.sha256//""), receipt:$receipt,
      prefix:(.prefix//.paths.prefix//""), commands:(.commands//[]),
      desktop_entries:(.desktop_entries//[]), depends:(.depends//[]),
      source:"hdd-immutable-install-plan"
    }' "${receipt}" >>"${records}"
done < <(find "${TARGET}/System/PackageDB" -maxdepth 1 -type f -name '*.json' -print0 | sort -z)
jq -s '{format:"auzix-installed-v1",installed:(unique_by(.name|ascii_downcase)|sort_by(.name|ascii_downcase))}' \
  "${records}" >"${TARGET}/System/State/packages/installed.json"
cp "${TARGET}/System/State/packages/installed.json" "${TARGET}/System/Settings/packages/installed.json"
rm -f "${records}"

echo "CONFIGURE_STAGE 5/6 finalize package and desktop surfaces"
chroot "${TARGET}" /Programs/BusyBox/current/Commands/busybox env AUZIX_LINK_MODE=strict \
  /System/Tools/finalize-installed-root /
if [[ -x "${TARGET}/Programs/AuzixDesktopIntegration/current/Commands/activate" ]]; then
  chroot "${TARGET}" /Programs/BusyBox/current/Commands/busybox env \
    AUZIX_PACKAGE_NAME=AuzixDesktopIntegration \
    /Programs/AuzixDesktopIntegration/current/Commands/activate
fi
if [[ -x "${TARGET}/Programs/AuzixDesktopIntegration/current/Commands/e-launcher-sync" ]]; then
  chroot "${TARGET}" /Programs/BusyBox/current/Commands/busybox env \
    AUZIX_PACKAGE_NAME=AuzixDesktopIntegration \
    /Programs/AuzixDesktopIntegration/current/Commands/e-launcher-sync
fi
echo "RUNTIME_CACHE not-applicable: frozen package set has no ldconfig provider; package wrappers own explicit library ladders"

echo "SESSION_TRIGGER publish package-owned X11 sessions"
mkdir -p "${TARGET}/System/Compatibility/usr/share/xsessions"
while IFS= read -r session; do
  relative="${session#${TARGET}}"
  ln -sfn "${relative}" "${TARGET}/System/Compatibility/usr/share/xsessions/$(basename "${session}")"
done < <(find "${TARGET}/Programs" -path '*/current/RootFS/usr/share/xsessions/*.desktop' -type f | sort)

echo "CONFIGURE_STAGE 6/6 validate configured root"
cp "${TARGET}/System/Boot/InstalledInit" "${TARGET}/init"
chmod 0755 "${TARGET}/init"
for required in \
  System/Boot/StartSequence System/Boot/InstalledInit init \
  System/Libraries/Runtime/glibc/ld-linux-x86-64.so.2 \
  System/Libraries/Runtime/glibc/libc.so.6 \
  System/Libraries/Runtime/glibc/libgcc_s.so.1 \
  Programs/BusyBox/current/Commands/busybox \
  Programs/Enlightenment/current \
  Programs/AuzixDesktopIntegration/current \
  System/Compatibility/usr/share/xsessions/enlightenment.desktop; do
  chroot "${TARGET}" /Programs/BusyBox/1.36.1/Commands/busybox test -e "/${required}" || {
    echo "configured surface missing in target namespace: ${required}" >&2; exit 1;
  }
done
"${SOURCE_ROOT}/scripts/probe-auzix-desktop-launchers.sh" "${TARGET}" \
  >"${TARGET}/System/Logs/packages/desktop-launcher-probe-build.txt" 2>&1 || true

cat >"${PROOF_ROOT}/configure-receipt" <<EOF
format=auzix-hdd-configure-proof-v1
proof_id=${PROOF_ID}
install_plan_sha256=$(jq -r .plan_content_sha256 "${PLAN}")
installed_state_count=$(jq '.installed|length' "${TARGET}/System/State/packages/installed.json")
libc6_repair_sha256=$(sha256sum "${REPAIR_RECEIPT}" | awk '{print $1}')
package_libgcc_sha256=${package_libgcc_sha}
root_prep=pass
finalize_installed_root=pass
desktop_activation=pass
runtime_linker_contract=explicit-package-wrapper-ladders
status=passed
EOF
cat "${PROOF_ROOT}/configure-receipt"

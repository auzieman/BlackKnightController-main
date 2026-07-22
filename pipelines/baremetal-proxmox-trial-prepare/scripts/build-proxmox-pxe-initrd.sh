#!/bin/sh
set -eu

iso_path=${1:?unattended Proxmox ISO path is required}
output_dir=${2:?PXE output directory is required}
expected_sha256=${3:?expected ISO SHA-256 is required}

test -s "$iso_path"
printf '%s  %s\n' "$expected_sha256" "$iso_path" | sha256sum -c -
install -d -m 0755 "$output_dir"

work_dir=$(mktemp -d "$output_dir/.bkc-pxeboot.XXXXXX")
mount_dir="$work_dir/iso"
mkdir -p "$mount_dir"

cleanup() {
  mountpoint -q "$mount_dir" && umount "$mount_dir" || true
  rm -rf "$work_dir"
}
trap cleanup EXIT HUP INT TERM

mount -o loop,ro "$iso_path" "$mount_dir"
test -s "$mount_dir/boot/linux26"
test -s "$mount_dir/boot/initrd.img"
test -e "$mount_dir/auto-installer-capable"
test -s "$mount_dir/answer.toml"

install -m 0644 "$mount_dir/boot/linux26" "$output_dir/linux26.part"
compression=$(file --mime-type --brief "$mount_dir/boot/initrd.img")
raw_initrd="$work_dir/initrd.raw"
case "$compression" in
  application/zstd|application/x-zstd)
    zstd -d -c "$mount_dir/boot/initrd.img" >"$raw_initrd"
    ;;
  application/gzip|application/x-gzip)
    gzip -dc "$mount_dir/boot/initrd.img" >"$raw_initrd"
    ;;
  *)
    echo "unsupported initrd compression: $compression" >&2
    exit 3
    ;;
esac

ln -s "$iso_path" "$work_dir/proxmox.iso"
(cd "$work_dir" && printf '%s\n' proxmox.iso | cpio -L -H newc -o) >>"$raw_initrd"

# A raw combined initrd exceeds iPXE's practical 2 GiB download boundary.
# Recompress the full cpio stream; the Proxmox kernel already supports zstd.
zstd -T0 -3 -c "$raw_initrd" >"$output_dir/initrd.part"
initrd_size=$(stat -c %s "$output_dir/initrd.part")
test "$initrd_size" -lt 2147483648 || {
  echo "compressed initrd still exceeds 2 GiB: $initrd_size" >&2
  exit 4
}

mv "$output_dir/linux26.part" "$output_dir/linux26"
mv "$output_dir/initrd.part" "$output_dir/initrd"
chmod 0644 "$output_dir/linux26" "$output_dir/initrd"
command -v chcon >/dev/null 2>&1 && chcon --reference="$iso_path" "$output_dir/linux26" "$output_dir/initrd" || true

sha256sum "$output_dir/linux26" "$output_dir/initrd"
echo "proxmox_pxe_initrd=ready output_dir=$output_dir"

#!/usr/bin/env sh
set -eu

target_interface="${1:-ens19}"
target_cidr="${2:-10.20.0.0/24}"

if [ "$target_interface" = "lo" ]; then
  echo "Refusing loopback interface for PXE provisioning." >&2
  exit 1
fi

case "$target_cidr" in
  10.*/*|172.16.*/*|172.17.*/*|172.18.*/*|172.19.*/*|172.20.*/*|172.21.*/*|172.22.*/*|172.23.*/*|172.24.*/*|172.25.*/*|172.26.*/*|172.27.*/*|172.28.*/*|172.29.*/*|172.30.*/*|172.31.*/*)
    ;;
  *)
    echo "PXE provisioning must stay on an isolated RFC1918 lab CIDR, got $target_cidr." >&2
    exit 1
    ;;
esac

if [ "$target_cidr" = "192.168.1.0/24" ]; then
  echo "Refusing to serve PXE on the management LAN." >&2
  exit 1
fi

echo "PXE prerequisite boundary accepted for $target_interface on $target_cidr."

#!/bin/sh
set -eu

esxi_host="${BKC_ESXI_HOST:-lab-esxi}"
ssh_config="${BKC_SSH_CONFIG:-/home/auzieman/Projects/BlackKnightController/ops/workstation-ssh/bkc-lab.conf}"
public_key_file="${BKC_OPERATOR_PUBLIC_KEY:-${HOME}/.ssh/id_ed25519.pub}"

test -s "${public_key_file}"
public_key="$(cat "${public_key_file}")"

sshpass -e ssh \
  -F "${ssh_config}" \
  -o PreferredAuthentications=password,keyboard-interactive \
  -o PubkeyAuthentication=no \
  -o StrictHostKeyChecking=no \
  "${esxi_host}" "mkdir -p /etc/ssh/keys-root; touch /etc/ssh/keys-root/authorized_keys; grep -qxF '${public_key}' /etc/ssh/keys-root/authorized_keys || echo '${public_key}' >> /etc/ssh/keys-root/authorized_keys; chmod 600 /etc/ssh/keys-root/authorized_keys; /etc/init.d/SSH restart >/dev/null 2>&1 || true; grep -qxF '${public_key}' /etc/ssh/keys-root/authorized_keys && echo '[auzix-esxi-key] operator key authorized for root'"

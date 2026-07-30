#!/usr/bin/env bash
set -euo pipefail

# Small lab ignition/parking wrapper.
#
# Intent:
#   - keep "wake the lab" and "park the noisy bare metal" discoverable;
#   - execute IPMI from ns1, because the BMCs live on the private lab side;
#   - reuse the existing Docker secret mounted in the running BKC container;
#   - avoid committing the lab iDRAC password into git.
#
# Usage:
#   ops/lab-power.sh status
#   ops/lab-power.sh start
#   ops/lab-power.sh park
#   ops/lab-power.sh check-autostart
#
# Optional overrides:
#   LAB_NS1=root@192.168.1.10
#   LAB_EDGE=root@192.168.1.15
#   LAB_IPMI_USER=root
#   LAB_IPMI_PASSWORD=...      # bypass Docker secret lookup
#   LAB_IPMI_PASSWORD_FILE_NS1=/root/.secrets/lab-ipmi-root-password
#   LAB_IPMI_SECRET_PATH=/run/secrets/bkc/BMC_R630_OPENSTACK_01_IDRAC.txt
#   LAB_BKC_CONTAINER=blackknight_bkc...  # optional; otherwise auto-detected

LAB_NS1="${LAB_NS1:-root@192.168.1.10}"
LAB_EDGE="${LAB_EDGE:-root@192.168.1.15}"
LAB_IPMI_USER="${LAB_IPMI_USER:-root}"
LAB_IPMI_PASSWORD_FILE_NS1="${LAB_IPMI_PASSWORD_FILE_NS1:-/root/.secrets/lab-ipmi-root-password}"
LAB_IPMI_SECRET_PATH="${LAB_IPMI_SECRET_PATH:-/run/secrets/bkc/BMC_R630_OPENSTACK_01_IDRAC.txt}"

declare -A BMC_HOSTS=(
  [server1]=10.20.0.119
  [server2]=10.20.0.102
)

declare -A HEALTH_TARGETS=(
  [server1-openstack]=10.20.0.240
  [server2-esxi]=10.20.0.114
  [esxi-swarm-mgr]=10.20.0.121
  [ipfire]=10.20.0.254
)

usage() {
  sed -n '1,30p' "$0" | sed 's/^# \{0,1\}//'
}

ipmi_password() {
  if [[ -n "${LAB_IPMI_PASSWORD:-}" ]]; then
    printf '%s' "$LAB_IPMI_PASSWORD"
    return
  fi
  if ssh -o BatchMode=yes -o ConnectTimeout=8 "$LAB_NS1" "test -s ${LAB_IPMI_PASSWORD_FILE_NS1@Q}"; then
    ssh -o BatchMode=yes -o ConnectTimeout=8 "$LAB_NS1" "cat ${LAB_IPMI_PASSWORD_FILE_NS1@Q}"
    return
  fi
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$LAB_EDGE" "
    set -e
    container=\${LAB_BKC_CONTAINER:-}
    if [ -z \"\$container\" ]; then
      container=\$(docker ps --filter name=blackknight_bkc. --format '{{.ID}}' | head -n1)
    fi
    test -n \"\$container\"
    docker exec \"\$container\" sh -lc 'cat ${LAB_IPMI_SECRET_PATH@Q}'
  "
}

ns1_ipmi() {
  local bmc="$1"
  local action="$2"
  local password
  password="$(ipmi_password)"
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$LAB_NS1" \
    "read -r pw; ipmitool -I lanplus -H ${bmc@Q} -U ${LAB_IPMI_USER@Q} -P \"\$pw\" chassis power ${action@Q}" <<<"$password"
}

status() {
  echo "BMC power:"
  for name in "${!BMC_HOSTS[@]}"; do
    bmc="${BMC_HOSTS[$name]}"
    printf '  %-8s %-13s ' "$name" "$bmc"
    ns1_ipmi "$bmc" status || true
  done | sort

  echo
  echo "Private lab reachability from ns1:"
  for name in "${!HEALTH_TARGETS[@]}"; do
    ip="${HEALTH_TARGETS[$name]}"
    if ssh -o BatchMode=yes -o ConnectTimeout=8 "$LAB_NS1" "ping -c1 -W1 ${ip@Q} >/dev/null"; then
      printf '  %-18s %-13s ok\n' "$name" "$ip"
    else
      printf '  %-18s %-13s down\n' "$name" "$ip"
    fi
  done | sort
}

start() {
  echo "Requesting chassis power on for known bare-metal lab hosts..."
  for name in "${!BMC_HOSTS[@]}"; do
    echo "  $name -> ${BMC_HOSTS[$name]}"
    ns1_ipmi "${BMC_HOSTS[$name]}" on || true
  done
  echo
  status
  echo
  echo "Next health gate: run the BKC pipeline 'LAB 00 — Bring Lab Online + Health Gate' from the UI."
}

park() {
  echo "Requesting graceful chassis soft-off for known bare-metal lab hosts..."
  for name in "${!BMC_HOSTS[@]}"; do
    echo "  $name -> ${BMC_HOSTS[$name]}"
    ns1_ipmi "${BMC_HOSTS[$name]}" soft || true
  done
  echo
  echo "Soft-off requested. Re-run 'ops/lab-power.sh status' in a minute to confirm."
}

check_autostart() {
  echo "Proxmox onboot/startup for core lab VMs:"
  ssh -o BatchMode=yes -o ConnectTimeout=8 root@192.168.1.9 '
    set -e
    for id in 127 128 129 118 124 140; do
      echo "vmid=$id"
      qm config "$id" | awk "/^(name|onboot|startup):/ {print}"
      qm status "$id"
      echo
    done
  '
  echo "Docker Swarm stacks on swarm1:"
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$LAB_EDGE" 'docker stack ls'
}

command="${1:-}"
case "$command" in
  status) status ;;
  start|up|on) start ;;
  park|soft-off|softoff) park ;;
  check-autostart|autostart) check_autostart ;;
  -h|--help|help|"") usage ;;
  *)
    echo "Unknown command: $command" >&2
    usage >&2
    exit 2
    ;;
esac

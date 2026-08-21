# Codex Lab Operator Memory

This is the quick scan file for Codex/operator sessions before touching lab
power, BKC runners, SSH tunnels, or private lab services.

## First rule

Do not infer the active control surface from memory. Check the runner and the
current repo notes first:

```bash
ops/lab-power.sh preflight
ops/lab-power.sh status
ops/lab-power.sh check-autostart
```

## IPMI rule

IPMI is always spoken from `ns1` into the lab-management side. Do not try to
infer BMC state from the laptop, edge BKC, public DNS, OpenStack, or Docker
contexts.

The pattern is deliberately boring:

```text
operator/current BKC runner
  -> ssh root@192.168.1.10  # ns1
      -> ipmitool -I lanplus -H <bmc-ip> -U root ...
```

`ns1` is also the truth source for DHCP leases, ARP/neighbors, and which host
claimed which address. If an OS IP is reachable but the BMC/IPMI IP is not,
state that split plainly; do not invent a different power-control story.

Use the current BlackKnightController checkout on the laptop or a current BKC
runner image. A stale edge BKC container may have an older `/app` checkout and
unseeded SSH host keys.

## BKC CLI / paper-trail rule

Use `bkc-cli` or a BKC-visible pipeline for remote actions by default. The goal
is to leave a receipt: rendered script/config, target, stdout/stderr, status,
validation, and run history.

Direct shell is acceptable for:

- read-only inspection;
- bootstrap checks when BKC itself is down;
- one-off hypothesis proof before converting the step to a pipeline.

Direct shell is not the durable fix path. If the command changes state and will
be repeated, fold it back into BKC/pipeline logic. See
`operator/bkc-cli-first.md` and `docs/codex-bkc-operating-notes.md`.

The laptop may not have BKC Python dependencies active. Prefer the BKC runtime
container or proper BKC service/CLI environment for `bkc-cli` actions.

## BKC render-first rule

BKC is not just a remote shell runner. Treat scripts, configs, package metadata,
desktop entries, system units, source fragments, and even small C/Lua/Python
programs as render targets.

The durable pattern is:

```text
intent JSON + target facts + Jinja/template
  -> BKC renders the exact artifact server-side
  -> BKC records the rendered artifact/checksum
  -> BKC stages it to the target
  -> BKC chmod/chown/compiles/places/runs as the artifact type requires
  -> BKC collects stdout/stderr/status/facts
  -> BKC cleans up or preserves evidence by policy
```

Do not think of interpreted scripts as monoliths. They are no different from an
HTML template: loopable, dynamic, argument-driven logic rendered on demand.

For AUZiX this means package-bot checks, installers, package hooks, menu
entries, validators, build scripts, and ISO/disk install runners should be
compiled from intent whenever they are target- or profile-dependent. AUZiX may
keep standalone sample scripts for users without BKC, but BKC mode should
prefer rendered artifacts.

If troubleshooting discovers a new rule, fold it back into one of:

- a JSON intent/defaults file;
- a Jinja/template artifact;
- a package contract;
- a BKC pipeline stage;
- an adapter rule for OS/package-tool behavior.

See `docs/architecture/bkc-intent-adapter-package-ops.md`.

## Mental map

- `.9` / `root@192.168.1.9`: Proxmox core VM state.
- `ns1` / `root@192.168.1.10`: private lab bridge, DHCP, PXE, IPMI execution,
  and routing vantage point.
- `lab-edge` / `root@192.168.1.15`: edge Docker swarm, nginx, registry,
  fallback BKC.
- `bkc.lab.auzietek.com`: intended OpenStack-hosted BKC, not the stale edge
  fallback container.
- `server1` / `10.20.0.240`: OpenStack/bare-metal lane.
- `server2-esxi` / `10.20.0.114`: ESXi host.
- `esxi-swarm-mgr` / `10.20.0.121`: ESXi-hosted Docker swarm manager.
- `r730-ai-01` / `10.20.0.130`: AI/Ollama worker OS address.
- `r730-ai-01` BMC / `10.20.0.115`: iDRAC/IPMI address.

## Usual hiding spots

- `ops/lab-power.sh` — lab wake, wait, park, preflight, `.9` autostart checks.
- `pipelines/lab-bringup-health-gate/README.md` — LAB 00 runbook and repair
  boundaries.
- `ops/workstation-ssh/README.md` — named SSH aliases, Docker contexts, and
  local tunnel ports.
- `ops/workstation-ssh/bkc-lab.conf` — exact SSH alias map.
- `operator/r730-ai-01-bringup.md` — R730 DHCP/PXE/BMC/IPMI notes.
- `pipelines/baremetal-ai-lab-worker-prepare/README.md` — R730 AI-worker role
  and BMC proof notes.
- `pipelines/ipfire-lab-edge-prepare/memory/fragments.json` — ns1/IPFire edge
  ownership guardrails.

## Runner sanity

If running from inside BKC:

```bash
pwd
git rev-parse --short HEAD 2>/dev/null || true
sed -n '1,80p' /app/ops/lab-power.sh
ops/lab-power.sh preflight
```

If the script only lists server1/server2 or lacks `wait`, `preflight`, r730, or
OpenWebUI checks, that container is stale. Rebuild/redeploy it or run from the
current repository checkout instead.

Fresh ephemeral containers should use `LAB_SSH_STRICT=accept-new`, the script
default. If a host was intentionally rebuilt and SSH says host identification
changed, repair the specific lab entry only:

```bash
ssh-keygen -R 192.168.1.10
ssh-keygen -R 192.168.1.15
ssh-keygen -R 192.168.1.9
```

Do not blanket-disable host-key checking for public or unknown hosts.

## Power/control boundaries

Allowed during normal bring-up:

- power on known bare-metal hosts through `ops/lab-power.sh start`;
- power on already-known VMs;
- restart already-known services;
- refresh stale SSH host keys for known lab hosts only;
- record evidence fragments.

Do not do these without explicit repair mode:

- reclone VMs;
- rerun destructive PXE;
- wipe or repartition disks;
- change firewall ownership/policy;
- rewrite DHCP reservations.

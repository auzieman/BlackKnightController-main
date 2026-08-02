# BlackKnightController Bare-Metal Hypervisor Demo

## The promise

Rebuild lab hardware like cattle, not pets.

BKC turns normal IT actions into repeatable pipelines:

- IPMI/iDRAC power control
- DHCP/PXE boot control
- Debian/Proxmox/OpenStack install steps
- post-install validation
- edge URLs and monitoring proof

## Lab topology

- `ns1` owns the private provisioning network and PXE services.
- `.9` is the current edge router path between LAN and lab-control.
- `server1` becomes the OpenStack host.
- `server2` becomes the Proxmox host.
- `swarm1` hosts BKC, monitoring, and the edge proxy.

## Recording run order

1. `00 VIDEO — Bare Metal Lab Reset / Preflight`
2. `10 VIDEO — Server1 Bare Metal + Trixie`
3. `10B VIDEO — Server1 Native OpenStack + Horizon`
4. `20 VIDEO — Server2 Proxmox Auto-Install`
5. `30 VIDEO — Seed Both Systems + Validate`

## 00 VIDEO — Preflight

Show that the lab is under control before the destructive work starts:

- BKC pipeline view filtered by `VIDEO`
- ns1 DHCP/PXE status
- iDRAC reachability for both servers
- edge landing page and Grafana activity view
- DHCP broad PXE default is disarmed

## 10 VIDEO — Server1 base install

Server1 is wiped and rebuilt by one unattended Debian Trixie PXE transaction.

Known-good guardrails:

- UEFI boot mode required
- exact Server1 MAC only
- no stale `.242` assumptions
- no serial-console boot-arg detour
- no custom wipe loop spiral
- local-disk first boot must prove SSH, identity, partitioning, and EFI boot

## 10B VIDEO — Native OpenStack

On the clean Trixie base, BKC runs reusable SSH fragments:

- Keystone + Horizon
- Glance + Placement
- Nova
- Neutron + Open vSwitch

Expected operator proof:

- Horizon loads
- `admin / changeme123`
- Keystone token works
- Nova and Neutron service lists respond

## 20 VIDEO — Server2 Proxmox

Server2 is wiped and rebuilt with Proxmox’s supported auto-installer path.

Known-good boundary:

- checksum-pinned unattended ISO
- exact Server2 MAC one-shot PXE
- installer handoff observed
- DHCP fragment disarmed
- next boot pinned to disk
- SSH, KVM, storage, root@pam API, and HTTPS validated

## 30 VIDEO — Seed and validate both systems

BKC proves it can control both deployed hypervisors:

- seed OpenStack project/user/flavor/network/image/server
- migrate or clone base guests on Proxmox
- validate OpenStack API and smoke server
- validate Proxmox guests and API
- validate edge URLs and Grafana visibility

## Fragments, not rabbit holes

The repeatable pattern is:

```text
prompt -> Codex -> pipeline -> evidence -> fragment memory
```

Direct SSH, SOL, Redfish, and tunnels are allowed for diagnosis, but final
readiness lives in checked-in pipeline logic.

## What this unlocks next

- migrate ns1 and swarm1 roles into the new hypervisor layer
- add OpenStack API control beside Proxmox control
- add pfSense-style edge appliance tests
- add OpenShift/k3s lab tracks
- add `40 VIDEO` local AI: Ollama + OpenWebUI + graph layout proposals

## Recording cue card

Use these cues during filming:

- `HOLD`: candidate work remains
- `RECORD NOW`: current code has a proof run
- `EDGE CHECK`: show landing page, Horizon, Proxmox, Grafana
- `RECEIPT`: show BKC run evidence, not a manual-only repair


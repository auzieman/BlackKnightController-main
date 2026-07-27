# Lab edge, switch fabric, and firewall direction

Status: current direction after the N2024 switch bring-up and pfSense parking

## Current working edge

The active, reliable edge/NAT role remains:

```text
ns1 -> iptables/NAT/DHCP/DNS/provisioning authority
```

This is the path to keep for recording and near-term demos. It is boring in the
best possible way: Linux-native, inspectable, scriptable, and already aligned
with BKC's SSH/pipeline model.

## Known-good service edge pattern

The service edge can stay small and app-aware while the big iron runs the
workloads:

```text
main swarm / lab-edge nginx
  -> HTTP UI links and reverse proxies

main swarm / monitoring Prometheus + Grafana
  -> scrape targets on OpenStack, ESXi swarm, Proxmox, and lab VMs

big iron
  -> runs the heavier app/control workloads
```

Current proof:

```text
micro-blog on ESXi Docker Swarm
  app UI:  http://10.20.0.121:18081
  metrics: http://10.20.0.121:9464/metrics
  edge UI: http://192.168.1.15:8091/
  public label: Micro Blog — ESXi Swarm
  Prometheus job: micro-blog-esxi-swarm
```

Validation captured on 2026-07-26:

- ns1 can reach the micro-blog UI and metrics endpoint on `10.20.0.121`
- edge Prometheus can scrape `10.20.0.121:9464`
- Prometheus target `micro-blog-esxi-swarm / 10.20.0.121:9464` reports `up`

This is the preferred near-term pattern for demo apps: expose the useful UI
through `lab-edge`, scrape metrics from edge Prometheus/Grafana, and only move
real routing/NAT into IPFire when a service needs network ownership rather than
simple HTTP reachability.

## Known-good Portainer edge pattern

Portainer stays on the main/edge swarm and attaches remote swarms through
Portainer Agent endpoints:

```text
ESXi Docker Swarm:
  tcp://swarm1.lab.auzietek.com:19091
  -> lab-edge stream
  -> 10.20.0.121:9001

OpenStack Docker Swarm:
  tcp://swarm1.lab.auzietek.com:19092
  -> lab-edge stream
  -> 10.20.0.230:9001
```

Validation captured on 2026-07-26:

- ESXi endpoint reports healthy in Portainer
- OpenStack endpoint was corrected from stale `tcp://10.20.0.240:19092` to
  `tcp://swarm1.lab.auzietek.com:19092`
- Portainer's Docker proxy can read OpenStack Docker info, 5 nodes, services,
  and containers through endpoint id `6`

Treat IPFire as the future NAT/edge owner for office-like behavior: published
service ports, alternate outside IPs, optional VLAN-tagged lanes through the
Lenovo/switch side, and later LDAP/OpenLDAP-style VIPs or sticky distribution.
Keep nginx for HTTP-aware behavior, redirects, cookies, headers, and friendly
operator URLs.

## Managed switch role

The Dell N2024 is now a first-class lab fabric node:

```text
hostname: n2024-lab
management IP: 10.20.0.100
access: serial recovery via Server1 /dev/ttyS1, SSH, HTTP
```

BKC should use it to collect:

- switch identity and firmware
- interface status
- MAC address table
- VLAN membership
- LLDP/CDP neighbors when available
- port-to-node evidence for Server1, Server2, ns1, and future appliance VMs

This is the bridge between physical cabling and BKC's resource graph.

## Firewall appliance direction

pfSense is parked as a future physical/appliance candidate. The VM installer
path was too unstable/manual for the current demo.

IPFire is the stronger next software-appliance candidate because it is
Linux-native and should fit the BKC model better:

```text
BKC -> Proxmox VM shell -> IPFire install/config -> firewall/NAT evidence
```

The captured candidate lane is:

```text
pipelines/ipfire-lab-edge-prepare
```

Expected IPFire lane:

1. create/clone a small two-NIC VM on Proxmox `.9`
2. attach IPFire installer media
3. assign RED to `vmbr0` / home operator side
4. assign GREEN to `vmbr20` / lab management side
5. keep DHCP disabled until cutover is explicit
6. validate NAT/firewall rules without displacing ns1
7. record switch-port and MAC evidence from N2024

This same candidate/cutover split should repeat later for Auzietek server
edge builds: stand up the new appliance beside the current edge, prove it, then
cut over only when the route, DHCP, DNS, NAT, service-forward, and rollback
fragments are all known-good.

## BKC-side product idea

The beta UI should show edge ownership explicitly:

```text
home LAN
  -> edge/NAT owner: ns1 today
  -> candidate appliance: IPFire
  -> switch fabric: N2024
  -> platforms: Proxmox, ESXi, OpenStack
  -> workloads: Docker swarms, OpenWebUI, BKC
```

Useful graph edges:

- `routes_through`
- `nat_owner`
- `dhcp_owner`
- `dns_owner`
- `connected_to`
- `observes_mac`
- `protects`
- `exposes_service`

The point is not just drawing a topology. The point is letting BKC answer:

```text
Who owns NAT right now?
Which switch port sees Server2?
What would break if we moved DHCP from ns1 to an appliance?
Which edge links expose ESXi, Horizon, Portainer, and OpenWebUI?
```

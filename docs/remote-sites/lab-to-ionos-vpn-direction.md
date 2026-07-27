# Lab to IONOS VPN direction

Status: candidate direction, not yet cut over

Date: 2026-07-26

## Decision

Use a containerized WireGuard gateway as the first lab-to-IONOS VPN path.

Keep IPFire as the office-style firewall/router appliance lane, preferably as a
VM or physical appliance, not as the first containerized site VPN.

The split is intentional:

```text
WireGuard container
  -> quick site-to-site tunnel
  -> easy BKC/SSH/Docker automation
  -> good for lab <-> IONOS reachability

IPFire VM/appliance
  -> office edge/firewall ownership
  -> NAT, firewall policy, service forwards, possible VPN UI
  -> stronger boundary than a container sharing the Docker host kernel
```

## Why not IPFire-in-a-container first?

IPFire wants to be the network appliance. A container can package tools, but it
does not become the same trust boundary as a VM/physical firewall because it
shares the host kernel and relies on host networking, capabilities, and iptables
state.

There are experimental/community Docker-IPFire attempts and IPFire has a Docker
Hub namespace, but this does not make "IPFire as the main routed container edge"
the cleanest first production-ish path.

For BKC's model, the clean version is:

```text
IPFire = appliance/VM/edge owner
WireGuard container = tactical site VPN service
```

Sources checked:

- IPFire community Docker support discussion:
  https://community.ipfire.org/t/feature-request-docker-support/3527
- IPFire community WireGuard discussion:
  https://community.ipfire.org/t/inquiry-regarding-the-inclusion-of-wireguard-vpn-in-ipfire/10899/11
- Experimental Docker IPFire image:
  https://github.com/GabLeRoux/docker-ipfire
- LinuxServer WireGuard container routing patterns:
  https://www.linuxserver.io/blog/routing-docker-host-and-container-traffic-through-wireguard

## Confirmed prerequisites

### Lab side

Checked on `ns1.lab.auzietek.com`:

```text
/dev/net/tun exists
wireguard kernel module available
net.ipv4.ip_forward = 1
```

### IONOS side

Checked on `74.208.45.165`:

```text
/dev/net/tun exists
wireguard kernel module available
net.ipv4.ip_forward = 1
```

This makes a Docker-hosted WireGuard service viable.

## Candidate topology

```text
lab site
  ns1 or IPFire candidate
  tunnel address: 10.90.0.1/24
  routed lab subnet: 10.20.0.0/24
  optional operator subnet: 192.168.1.0/24

IONOS site
  ionos-auzietek-01 / 74.208.45.165
  tunnel address: 10.90.0.2/24
  routed docker/service subnets:
    - 172.17.0.0/16
    - 172.18.0.0/16
    - 172.19.0.0/16
    - selected host-published services only
```

Prefer conservative routing first:

```text
lab -> IONOS:
  allow SSH, Prometheus scrape, BKC probes, selected app/admin ports

IONOS -> lab:
  allow BKC callback/control only if needed
  avoid broad access to the home/operator LAN
```

## Candidate container shape

Use a WireGuard container with:

```text
network_mode: host
cap_add:
  - NET_ADMIN
  - SYS_MODULE
devices:
  - /dev/net/tun:/dev/net/tun
volumes:
  - /srv/wireguard:/config
```

For a first BKC-controlled path, prefer `linuxserver/wireguard` or `wg-easy`
depending on whether we want:

```text
linuxserver/wireguard -> simpler config-as-files / automation
wg-easy               -> easier web UI for manual peer checks
```

BKC should render the WireGuard configs from secret-backed peer keys, ship them
to both sides, bring the tunnel up, then validate:

```text
ping 10.90.0.1 <-> 10.90.0.2
ssh 10.90.0.2 from lab BKC
curl selected IONOS service from lab over tunnel
Prometheus scrape selected IONOS exporter over tunnel
```

## BKC resource model

Candidate graph objects:

```text
site: lab
site: ionos-auzietek
edge: ns1
edge: ipfire-lab-edge-candidate
host: ionos-auzietek-01
host: ionos-auzietek-02
vpn: lab-ionos-wireguard
secret: ionos/wireguard/site-key
secret: lab/wireguard/site-key
```

Useful relationships:

```text
site_routes_to
vpn_peer
exposes_subnet
protects
scrapes
controlled_by
```

## Pipeline candidate

Future pipeline:

```text
remote-ionos-wireguard-site-vpn
```

Stages:

1. confirm host prerequisites on lab and IONOS
2. create/read WireGuard peer secrets
3. render lab and IONOS configs
4. stage config under `/srv/wireguard`
5. deploy WireGuard container on both sides
6. add minimal firewall/routing rules
7. validate tunnel, SSH, metrics, and selected service reachability
8. record BKC graph fragments

Cutover should be explicit. Do not make IONOS reachable to all lab/private
subnets until the allow-list is documented.


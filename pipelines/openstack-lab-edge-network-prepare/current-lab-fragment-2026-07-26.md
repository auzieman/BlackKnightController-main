# Current Lab Edge / Provider Fragment — 2026-07-26

Status: observed / candidate-known-good direction

## Physical convention

Facing the managed switch:

```text
left side   service / uplink / operator-visible traffic
right side  management / provisioning / BMC / LOM control traffic
```

The switch is still effectively flat for this phase. This note records cabling
intent so BKC, future operators, and AI assistants do not confuse physical
layout with enforced VLAN policy.

## Live network truth

```text
192.168.1.0/24     home LAN / operator side
10.20.0.0/24       lab management + provisioning + current service fabric
172.24.10.0/24     OpenStack tenant/internal network
```

Older docs and exploratory defaults may still mention `10.1.x`; treat those as
design ancestry unless a pipeline explicitly overrides the current lab values.

## OpenStack service-side direction

The preferred pattern for BKC/OpenWebUI/swarm service VMs is dual-homed:

```text
eth0  tenant/internal  172.24.10.x
eth1  service/provider 10.20.0.x
```

This is cleaner than depending on namespace-only SSH into
`qdhcp-edeb7fd2-0add-4df7-a428-811a9fa887ec` for normal operations.

Observed Server1/OpenStack hints:

- `lab-internal` exists on `172.24.10.0/24`.
- No OpenStack router/provider network is active yet.
- `br-ex` exists.
- Neutron ML2 allows `flat_networks = provider`.
- OVS agent maps `bridge_mappings = provider:br-ex`.
- `br-ex` is mapped to the secondary service-side NIC path.

## First live service/provider VM attachment

Created and validated:

```text
network: lab-service-provider
type: flat
physical network: provider
subnet: lab-service-provider-v4
cidr: 10.20.0.0/24
dhcp: disabled
allocation/reservation lane: 10.20.0.230-10.20.0.239

server: bkc-swarm-mgr-01
tenant ip: 172.24.10.183
service ip: 10.20.0.230
service port: bkc-swarm-mgr-01-service
service mac: fa:16:3e:6f:2c:6f
```

Because ns1 owns DHCP on the flat lab fabric, OpenStack DHCP must stay disabled
on this provider/service subnet unless the DHCP ranges are made deliberately
non-overlapping and isolated. Service IPs must be recorded in ns1 as claimed
static/reserved addresses. The first reservation lives on ns1 in:

```text
/etc/dhcp/dhcpd.d/bkc-service-statics.conf
```

Guest route nuance: `bkc-swarm-mgr-01` had a DHCP-learned tenant route for
`10.20.0.10` via `ens3`, which caused asymmetric replies to ns1. The service
NIC config pins lab service traffic to `ens7`:

```yaml
network:
  version: 2
  ethernets:
    ens7:
      dhcp4: false
      dhcp6: false
      addresses:
        - 10.20.0.230/24
      routes:
        - to: 10.20.0.0/24
          scope: link
          metric: 10
        - to: 10.20.0.10/32
          scope: link
          metric: 1
```

Validation:

- Server1 can ping `10.20.0.230`.
- `bkc-swarm-mgr-01` can ping ns1 `10.20.0.10` through `ens7`.
- ns1 can ping `10.20.0.230` and sees TCP/22 open.
- BKC container direct SSH to `10.20.0.230` initially timed out because swarm
  traffic arrived from the home/Docker-overlay side and the VM had no natural
  return path for those sources through the service NIC.

Temporary edge SNAT on Proxmox/edge `.9` restored reachability:

```bash
iptables -t nat -A POSTROUTING \
  -s 192.168.1.0/24 -d 10.20.0.230/32 -o vmbr20 \
  -j SNAT --to-source 10.20.0.9

iptables -t nat -A POSTROUTING \
  -s 10.0.0.0/8 -d 10.20.0.230/32 -o vmbr20 \
  -j SNAT --to-source 10.20.0.9
```

After SNAT, both the swarm1 host and the BKC container could SSH directly to
`admin-deploy@10.20.0.230`. Treat this as a working lab bridge, not the final
architecture. Durable placement should move this behavior into the selected
edge/firewall path, likely IPFire or a BKC-managed edge rule set.

## Decision

Before dogfooding the OpenStack-hosted BKC as a normal service, create/validate
a flat provider/service network and attach selected VMs to it as a second NIC.
Expose the resulting `10.20.0.x` service address through IPFire/nginx/edge.

Ceph/storage remains intentionally parked until the service/provider network and
second-node lifecycle are boringly repeatable.

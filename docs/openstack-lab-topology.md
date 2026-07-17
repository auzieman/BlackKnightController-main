# OpenStack Lab Topology

The OpenStack lab should be treated as a routed island behind controlled entry
points, not as another flat extension of the home LAN.

## Naming

Preferred DNS suffix:

```text
lab.morgans.home.arpa
```

Avoid using `.local` unless we intentionally disable or work around mDNS
behavior. `.local` is commonly claimed by multicast DNS and tends to create
awkward split-DNS failures on Linux desktops, macOS, and some appliances.

Useful aliases can still be created for demo friendliness:

```text
bkc.lab.morgans.home.arpa
grafana.lab.morgans.home.arpa
openstack.lab.morgans.home.arpa
horizon.lab.morgans.home.arpa
```

## Network Shape

Initial subnets:

```text
192.168.1.0/24     home LAN, router DHCP remains owner
10.1.0.0/24        provisioning / PXE / installer network
10.1.1.0/24        management / BMC-adjacent / host control network
10.1.2.0/24        service/API network for BKC, Grafana, Horizon, internal apps
10.1.3.0/24        tenant/provider experiment network, optional at first
```

The exact VLAN IDs can be assigned once the managed switch is in hand. The BKC
model should store the subnet role independently from the VLAN number so we can
renumber without breaking pipeline intent.

The first managed switch target is the Dell PowerConnect N3048. BKC should
discover it read-only first, then connect switch ports, VLAN roles, observed
MACs, and PXE events to the physical server model. See
`docs/switch-discovery-and-pxe.md` and
`pipelines/n3048-switch-discovery-prepare/`.

## Edge Pattern

Use a small number of intentional entry points:

- `ns1`: provisioning router, DNS authority/forwarder, DHCP/PXE/HTTP image
  service, and temporary route anchor.
- `bastion-01`: SSH and operator entry point into the 10.1.x networks.
- `nginx-edge-01`: reverse proxy for browser-facing lab services.

Most OpenStack hosts and services should not need direct presence on
`192.168.1.0/24`. Browser and API exposure should flow through `nginx-edge-01`
or a later OpenStack load balancer/provider network.

## Service Placement

Candidate migration targets:

```text
BKC API/UI           -> 10.1.2.x behind nginx-edge-01
Grafana              -> 10.1.2.x behind nginx-edge-01
Loki/metrics stack   -> 10.1.2.x internal first, proxied only where useful
Registry/cache       -> 10.1.2.x internal
Horizon              -> 10.1.2.x behind nginx-edge-01
OpenStack APIs       -> 10.1.2.x internal/proxied as needed
Bastion SSH          -> 192.168.1.x plus 10.1.1.x
```

This gives a clean demo story: the home LAN can reach a few stable names, while
the actual lab platform remains its own managed network.

## Workstation Routing and DNS

There is no universal `/etc/routes.d` standard across Linux desktops. The most
reliable workstation pattern is:

1. Keep the workstation on normal home LAN DHCP.
2. Add persistent static routes for lab subnets through the lab router/bastion.
3. Add split DNS for `lab.morgans.home.arpa`.

If the workstation uses NetworkManager, store the routes on the home LAN
connection:

```bash
nmcli connection modify "Wired connection 1" +ipv4.routes "10.1.0.0/16 192.168.1.10"
nmcli connection modify "Wired connection 1" +ipv4.dns "192.168.1.10"
nmcli connection modify "Wired connection 1" +ipv4.dns-search "lab.morgans.home.arpa"
nmcli connection up "Wired connection 1"
```

If we use `systemd-resolved`, split DNS can be made more explicit:

```bash
resolvectl dns <interface> 192.168.1.10
resolvectl domain <interface> "~lab.morgans.home.arpa"
```

For BKC automation, a small workstation-prep pipeline should render one of
these patterns based on the detected network manager rather than hand-editing
global resolver files.

## BKC Modeling

Represent the route and DNS pieces as first-class resources:

```text
network:home-lan
network:lab-provisioning
network:lab-management
network:lab-services
network:lab-tenant-provider

node:vm:ns1
  -> routes_to -> network:lab-provisioning
  -> routes_to -> network:lab-management
  -> provides_dns -> zone:lab.morgans.home.arpa
  -> provides_pxe -> network:lab-provisioning

node:vm:bastion-01
  -> bridges_access_to -> network:lab-management

node:vm:nginx-edge-01
  -> proxies -> service:bkc
  -> proxies -> service:grafana
  -> proxies -> service:horizon
```

The Resource Graph should eventually show this as home LAN on the left, edge
nodes in the middle, and OpenStack/VMware lab networks on the right.

## First Pipeline Slice

The next useful pipeline should be review-first:

1. Declare the DNS zone and subnets.
2. Validate ns1 can route to each lab subnet.
3. Validate workstation route and split-DNS intent.
4. Plan `bastion-01`.
5. Plan `nginx-edge-01`.
6. Record service migration targets for BKC, Grafana, registry/cache, and
   Horizon.

Actual route mutation should stay gated until the switch/VLAN details and final
ns1 interface mapping are known.

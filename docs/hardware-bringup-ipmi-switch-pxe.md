# Hardware Bring-Up: IPMI, Switch, and PXE

This note captures the first-pass operating procedure for bringing the new lab
hardware under BKC management. It should stay practical and vendor-aware without
turning into a pile of one-off shell history.

## Goal

The goal is to make physical infrastructure manageable before an operating
system exists:

```text
server arrives
  -> BMC/iDRAC reachable
  -> switch port known
  -> MAC address observed
  -> PXE path validated
  -> installer selected
  -> first boot enrolled
  -> host becomes a managed BKC resource
```

## First Rack Notes

Expected first hardware:

- Dell R630-class servers for OpenStack and VMware evaluation tracks.
- Dell PowerConnect N3048 managed switch.
- Existing `ns1` VM as the first DNS, route, DHCP/PXE, and image service anchor.
- Workstation remains on home LAN DHCP with persistent lab routes and split DNS.

Preferred lab DNS suffix:

```text
lab.morgans.home.arpa
```

Avoid `.local` for the lab zone because it commonly collides with multicast
DNS.

## Network Shape

Initial network intent:

```text
192.168.1.0/24     home LAN, still owned by the home router DHCP
10.1.0.0/24        provisioning / PXE / installer network
10.1.1.0/24        management / BMC-adjacent / host control network
10.1.2.0/24        service/API network for BKC, Grafana, Horizon, registry
10.1.3.0/24        provider / tenant experiment network
```

Keep the 10.1.x networks behind deliberate entry points:

- `ns1`: route/DNS/PXE/image anchor.
- `bastion-01`: SSH/operator entry point.
- `nginx-edge-01`: browser/API reverse proxy into lab services.

## IPMI / iDRAC Bring-Up

For each server, collect and record these facts before PXE install:

- vendor/model
- service tag / serial number
- asset label
- iDRAC/BMC MAC address
- iDRAC/BMC management IP
- iDRAC/BMC credential reference, never raw password in Git
- boot NIC MAC address
- switch port for BMC, if cabled separately
- switch port for PXE/host NIC
- intended platform track: OpenStack, VMware, rescue, or spare

Sanitized BKC node shape:

```json
{
  "node_id": "node:physical_machine:r630-openstack-01",
  "model": "Dell PowerEdge R630",
  "role": "openstack-controller-compute",
  "bmc_node_id": "node:bmc:r630-openstack-01-idrac",
  "bmc_provider": "redfish-or-ipmi",
  "provisioning_nic_node_id": "node:network_interface:r630-openstack-01-lom1",
  "provisioning_mac": "runtime-dictionary-value",
  "management_address": "10.1.1.31"
}
```

BKC should validate BMC reachability before any destructive install. Power,
boot-order, or virtual-media changes should stay gated until identity and MAC
facts match.

## N3048 Switch Bring-Up

Treat the Dell PowerConnect N3048 as a managed resource, not passive cabling.

First-pass facts:

- management address
- credential reference
- model and firmware
- port list
- VLAN summary
- uplink port
- ns1 ports
- R630 BMC and PXE ports
- observed LLDP/CDP neighbors if available
- observed MAC table

Initial port intent can be approximate and corrected after discovery:

```text
gi1/0/1    home uplink
gi1/0/2    ns1 provisioning/uplink path
gi1/0/3    ns1 management path
gi1/0/11   r630-openstack-01 PXE/host NIC
gi1/0/12   r630-openstack-02 PXE/host NIC
gi1/0/21   r630-vmware-01 PXE/host NIC
```

The first switch workflow should be read-only:

1. Register switch node.
2. Declare VLAN and port intent.
3. Validate management reachability.
4. Collect interface, VLAN, LLDP, and MAC-table observations.
5. Correlate observed MACs to BKC NIC nodes.
6. Record switch-port evidence.
7. Use switch-port evidence as a gate for PXE install.

Do not push switch configuration until backups/export and recovery access are
known.

## PXE Gates

Before enabling a destructive PXE install, require:

- physical node identity exists
- BMC/iDRAC is reachable
- boot NIC MAC is known
- switch port is known or observed
- DHCP/PXE services are scoped to the lab provisioning network
- installer asset checksum is valid
- boot is one-shot or PXE state is cleared after first boot

Expected BKC events:

```text
discovery.detected
bmc.reachable
switch.management_reachable
switch.mac_table_observed
dhcp.lease_observed
pxe.boot_request
image.checksum_validated
installer.started
first_boot.observed
enrollment.completed
validation.passed
```

## Pipeline Map

Relevant review-first recipes:

- `pipelines/openstack-lab-edge-network-prepare/`
- `pipelines/n3048-switch-discovery-prepare/`
- `pipelines/baremetal-r630-pxe-validation/`
- `pipelines/baremetal-openstack-lab-prepare/`
- `pipelines/baremetal-vmware-trial-prepare/`
- `pipelines/trixie-openstack-host-prepare/`
- `pipelines/vmware-k3s-lab-prepare/`

These should stay in review-first mode until runtime dictionaries contain real
MACs, BMC addresses, switch management details, credential references, and
operator approvals.

## Runtime Dictionary Rule

Commit portable intent to Git:

- node types
- relationship types
- expected evidence
- stage order
- sanitized example labels

Keep lab-local facts in runtime dictionaries:

- serial numbers
- MAC addresses
- switch management IP
- credential references
- VLAN IDs if they are lab-specific
- installer media paths
- IP assignments

That keeps the public repository useful while preserving the real lab state in
the mounted BKC runtime volume.

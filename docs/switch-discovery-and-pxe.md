# Switch Discovery and PXE

The Dell PowerConnect N3048 should become a managed BKC resource before it is
used as the lab switch. For bare-metal provisioning, a switch is not just
background network gear. It is evidence for physical node identity.

## Device

```text
Model: Dell PowerConnect N3048
Ports: 48 gigabit access ports
Role: lab managed switch for PXE, management, service, and provider networks
```

Repository examples should use sanitized identity values. Serial number,
management address, credentials, firmware, and final VLAN IDs belong in runtime
dictionaries after the switch is physically installed.

## Why It Matters

PXE discovery can start from any of these facts:

- switch management identity
- switch port
- observed MAC address
- LLDP/CDP neighbor
- DHCP lease
- PXE boot request
- BMC/iDRAC address
- physical server serial number

The useful BKC behavior is to join those facts into one resource story:

```text
switch:n3048-lab
  -> has_port -> switch_port:gi1/0/1
  -> observes_mac -> network_interface:r630-openstack-01-lom1
  -> attached_to -> physical_machine:r630-openstack-01
  -> uses_vlan -> network:lab-provisioning
  -> produces_evidence -> pxe.boot_request
```

This gives us a way to manage a server before an OS exists and before we fully
trust DHCP hostnames.

## Initial VLAN Intent

The exact VLAN numbers can change, but the roles should stay stable:

```text
home-uplink          home LAN / operator access
lab-provisioning     PXE, DHCP, TFTP/iPXE, installer HTTP
lab-management       BMC, host management, bastion SSH
lab-services         BKC, Grafana, registry/cache, Horizon, APIs
lab-provider         OpenStack/VMware provider or tenant experiment traffic
```

The switch discovery pipeline should validate the declared role and not require
the first implementation to push switch configuration. Configuration mutation
should be a separate gated stage once credentials, backups, and out-of-band
access are confirmed.

## Discovery Evidence

Minimum evidence to collect:

- switch reachable on management address
- model and firmware observed
- interface list observed
- VLAN summary observed
- LLDP neighbors observed when available
- MAC address table observed
- intended PXE ports mapped to physical nodes
- uplink and management paths identified

Later provider adapters can use SSH, SNMP, RESTCONF, or vendor CLI parsing. The
pipeline model should not depend on one transport.

## First Safe Workflow

1. Register the switch as `node:switch:n3048-lab`.
2. Register planned port roles without mutating the switch.
3. Validate management reachability.
4. Collect read-only interface, VLAN, LLDP, and MAC-table observations.
5. Correlate observed MACs with known BMC/NIC/runtime dictionaries.
6. Record `attached_to` relationships between switch ports and physical NICs.
7. Use those relationships as a gate before destructive PXE installation.

This should become one of the first visual wins in the Resource Graph: rack,
switch, ports, physical servers, NICs, BMCs, provisioning network, and PXE
events all connected before any OS exists.

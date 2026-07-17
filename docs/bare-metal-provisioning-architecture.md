# Bare Metal Provisioning Architecture

BlackKnightController should be able to manage a physical server before an
operating system exists. The first manageable identity may be a serial number,
BMC address, asset tag, switch port, or NIC MAC address rather than a hostname.

## Lifecycle

```text
Physical Hardware
  -> Provisioning Services
  -> Installation
  -> Validation
  -> Managed Resource
  -> Application Deployment
```

The lifecycle is event driven. Each transition should leave evidence that can
be shown in the Resource Graph, pipeline runs, and node detail pages.

## Physical First Model

Bare metal provisioning starts with these node types:

- `physical_machine`: chassis/server identity, serial number, asset tag, vendor,
  model, rack/slot, desired role, and power state.
- `bmc`: IPMI, Redfish, or vendor management controller endpoint.
- `network_interface`: NIC identity, MAC address, switch port, VLAN, observed
  DHCP lease, and boot capability.
- `network_switch`: managed switch identity, model, management endpoint,
  firmware, read-only observation transport, VLAN intent, and port inventory.
- `switch_port`: physical port identity, expected role, observed MACs,
  LLDP/CDP neighbor evidence, VLAN membership, and attached node relationship.
- `provisioning_profile`: desired installer, boot image, kickstart/preseed,
  unattend file, rescue image, post-install enrollment steps, and validation
  gates.
- `image_asset`: installer kernel/initrd, ISO, WinPE/WIM, ESXi bundle, Proxmox
  ISO, rescue image, checksum, and local cache path.
- `validation_evidence`: observed proof such as DHCP lease, HTTP checksum,
  installer callback, first boot SSH banner, host key, or final role check.

These nodes can exist without an operating system. That is the key distinction
from VM or SSH inventory.

## Relationship Direction

Use explicit directional relationships so graph layout does not infer topology
from names:

```text
physical_machine -> controlled_by -> bmc
physical_machine -> has_interface -> network_interface
network_interface -> attached_to -> network_switch
network_switch -> has_port -> switch_port
switch_port -> observes_mac -> network_interface
switch_port -> uses_vlan -> network
network_interface -> uses -> network
service:dhcpd -> provides_dhcp -> network
service:pxe -> serves_pxe -> network
provisioning_profile -> uses -> image_asset
pipeline -> applies_profile -> provisioning_profile
physical_machine -> booted_from -> image_asset
validation_evidence -> validates -> physical_machine
```

Provider-specific observations can add `observed_on`, `runs_on`, or
`member_of`, but they should not replace the physical identity.

## Provisioning Events

Provisioning should emit normalized events. A run can fail at any layer while
still preserving useful evidence.

Recommended event types:

- `discovery.detected`
- `bmc.reachable`
- `power.requested`
- `dhcp.lease_observed`
- `pxe.boot_request`
- `ipxe.script_rendered`
- `image.selected`
- `image.checksum_validated`
- `installer.started`
- `installer.callback`
- `installer.completed`
- `first_boot.observed`
- `enrollment.started`
- `enrollment.completed`
- `validation.passed`
- `validation.failed`
- `provisioning.completed`
- `provisioning.failed`

Example event:

```json
{
  "event_type": "dhcp.lease_observed",
  "node_id": "node:network_interface:r630-01-lom1",
  "run_id": "run:baremetal-r630-01-001",
  "observed_at": "2026-07-11T12:00:00Z",
  "evidence": {
    "mac": "b8:ca:3a:00:10:01",
    "ip": "10.20.0.141",
    "dhcp_server": "node:service:ns1-dhcpd",
    "lease_file": "/var/lib/dhcp/dhcpd.leases"
  }
}
```

## Validation Layers

Validation should be layered and reusable. A provisioning pipeline should be
able to stop after any layer and still report what is known.

Infrastructure validation:

- DHCP service is configured for the intended interface and subnet.
- TFTP/iPXE/HTTP assets exist and are readable.
- Image checksums match declared values.
- BMC/IPMI/Redfish endpoint is reachable.
- Switch/VLAN/network path is plausible.
- Switch port evidence links the expected physical port to the expected NIC MAC
  address before destructive PXE install starts.

Installation validation:

- PXE boot request observed for the expected MAC address.
- Correct iPXE script or boot menu entry selected.
- Installer starts and reaches callback/progress endpoint when supported.
- Reboot occurs and PXE one-shot state is cleared.

Enrollment validation:

- SSH, WinRM, Redfish, VMware API, Proxmox API, or other target endpoint is
  reachable.
- Host identity matches expected serial, MAC, hostname, or generated machine id.
- Desired base configuration is present.
- Node is registered with BKC and linked to the original physical identity.

Operational validation:

- Role-specific service checks pass.
- Monitoring/remote support agents are online when declared.
- BKC can rerun validation without destructive side effects.

## Provider Boundary

Provisioning providers should be adapters, not the core model:

- BMC providers: IPMI, Redfish, vendor-specific APIs.
- Boot providers: DHCP, TFTP, HTTP, iPXE script generation.
- Installer providers: Debian preseed, Fedora kickstart, Windows unattended,
  WinPE, VMware ESXi, Proxmox, future OpenStack.
- Validation providers: shell checks, HTTP checks, checksum checks, API checks,
  callback receivers.

The core system should store nodes, relationships, events, evidence, and
desired state. Providers execute work and report evidence.

## Small Office Proving Ground

The small office reference architecture remains the proving ground:

- hypervisor host
- Windows administrative workstation
- Windows IIS application server
- Linux developer workstation
- identity provider
- shared storage
- monitoring
- remote support

The long-term target is that every component can be reproduced from bare metal
with declarative configuration, validation gates, and observable events.

## Physical Hardware Tracks

The first real hardware arrival should split into two visible tracks while
sharing the same lower provisioning lifecycle:

- OpenStack lab: PXE install a base OS, enroll with BKC SSH, validate hardware
  baseline facts, then hand off to an OpenStack installer provider.
- VMware evaluation: use operator-supplied VMware evaluation media, render an
  ESXi-style unattended install intent, validate the management endpoint after
  first boot, and optionally register the host with a trial vCenter.

Both tracks must start from the same physical-first graph:

```text
physical_machine
  -> controlled_by -> bmc
  -> has_interface -> provisioning_nic
  -> uses -> provisioning_profile
  -> booted_from -> image_asset
  -> produces -> validation_evidence
```

Repository examples must stay sanitized. Serial numbers, MAC addresses, BMC
addresses, image checksums, license keys, and secrets should be supplied through
local dictionaries or runtime inventory before a destructive install can run.

## First Implementation Slice

The next practical slice should avoid trying to install every OS at once:

1. Declare one physical-machine example with BMC, NIC, provisioning profile, and
   image assets.
2. Add a validation-only pipeline that checks BMC reachability, DHCP boundary,
   HTTP assets, and image checksums.
3. Add event records for DHCP lease and PXE boot request observations.
4. Render those nodes and events in the Resource Graph without requiring an OS.
5. Reuse the same profile shape for Debian Trixie first, then Windows/WinPE.

The first folder-backed example is
`pipelines/baremetal-r630-pxe-validation/`. It is intentionally
evidence-oriented and should not power on, reboot, or install hardware until
the BMC and boot provider actions are implemented.

The first delivery-day track recipes are
`pipelines/baremetal-openstack-lab-prepare/` and
`pipelines/baremetal-vmware-trial-prepare/`. They extend the same validation
model into OpenStack and VMware evaluation planning without committing
operator-specific media or credentials.

The first managed-switch discovery recipe is
`pipelines/n3048-switch-discovery-prepare/`. It prepares read-only discovery for
the Dell PowerConnect N3048 so PXE events can be tied back to physical switch
ports, observed MACs, and declared node intent.

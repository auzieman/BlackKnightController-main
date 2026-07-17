# Hardware Arrival: OpenStack and VMware Tracks

The incoming R630-class hardware should be treated as physical inventory before
it becomes hypervisor capacity. BKC should know the server, BMC, NICs, MAC
addresses, provisioning profile, image assets, and validation evidence before a
disk is touched.

## Track Split

Two tracks are prepared:

- OpenStack lab track: prove BKC can PXE install a base OS, enroll the host,
  validate the hardware baseline, and hand off to an OpenStack installer
  provider.
- VMware evaluation track: prove BKC can PXE install or prepare an ESXi-style
  host from operator-supplied evaluation media, validate first boot, and
  optionally register it with a trial vCenter.

These tracks should share the same lower layers: BMC discovery, DHCP/PXE/HTTP
delivery, one-shot boot intent, image checksum validation, installer events,
first boot evidence, and BKC enrollment.

## Delivery-Day Checklist

1. Record each server serial number, asset tag, iDRAC/BMC address, and rack
   label.
2. Record each provisioning NIC MAC address before enabling destructive PXE
   install.
3. Decide whether the first host is assigned to OpenStack, VMware, or a
   temporary rescue profile.
4. Validate ns1 provisioning services on the isolated provisioning network.
5. Cache only legal/operator-supplied installer media and checksums.
6. Run validation-only BKC pipelines before enabling disk writes.
7. Enable one-shot PXE install only after BMC and MAC identity match.
8. Clear PXE install state after first boot so hosts do not reinstall.
9. Enroll the installed host with the appropriate control plane: BKC SSH for a
   Linux base OS, VMware API/SSH for ESXi-style hosts.
10. Record evidence in the Resource Graph and pipeline run history.

## OpenStack Preparation

The OpenStack lane should initially focus on host readiness rather than full
cloud deployment. The useful first proof is:

```text
physical_machine
  -> bmc reachable
  -> provisioning NIC identified
  -> base OS image validated
  -> one-shot PXE intent rendered
  -> base OS installed
  -> BKC SSH enrolled
  -> Trixie Neutron/Open vSwitch host prep completed
  -> virtualization, disk, NIC, and time checks passed
  -> OpenStack installer inventory produced
```

Installer choice should remain a provider boundary. Kolla Ansible, OpenStack
Ansible, or another installer can consume the same normalized host facts later.

The first concrete host-prep recipe is
`pipelines/trixie-openstack-host-prepare/`. It installs the OpenStack client,
Neutron host packages, Open vSwitch, kernel module settings, and sysctl values,
but leaves Neutron agent activation deferred until the control plane provider
supplies RabbitMQ, auth, and ML2 configuration.

Before touching the R630s, BKC can smoke test the package/config path with
`pipelines/trixie-openstack-package-smoke/` against a clone or reuse of the
foo.bar SuiteCRM Trixie VM. That proves the repeatable install/config mechanics
without claiming the VM can run a real OpenStack role.

## VMware Evaluation Preparation

The VMware lane should keep repository data sanitized. VMware installation
media, licenses, and evaluation entitlements are operator-supplied and should
not be committed.

Current lab media reference:

```text
/home/auzieman/Projects/blackknightcontroller-vmware/VMware-VMvisor-Installer-8.0U3e-24677879.x86_64.iso
sha256: 9782c96ffd01cc56da17ec31573da69f4cba2f9402e67c8b55d05d9472c7376a
```

This path is a local operator media cache. BKC may validate and stage from it,
but the ISO must remain outside git.

The useful first proof is:

```text
physical_machine
  -> bmc reachable
  -> provisioning NIC identified
  -> operator-supplied installer asset validated
  -> ESXi-style kickstart intent rendered
  -> one-shot PXE install planned
  -> management endpoint reachable
  -> datastore and management NIC evidence recorded
  -> optional vCenter registration planned
```

This keeps the enterprise demonstration track realistic without hard-coding
VMware-specific assumptions into the core provisioning model.

## Prepared Recipes

- `pipelines/baremetal-openstack-lab-prepare/`
- `pipelines/baremetal-vmware-trial-prepare/`

Both recipes are draft/review-first. They intentionally do not expose runnable
install buttons until real BMC credentials, MAC addresses, image checksums, and
provider actions are in place.

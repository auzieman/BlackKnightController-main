# Bare Metal OpenStack Lab Prepare

Review-first preparation lane for turning the incoming R630-class hardware into
an OpenStack lab track.

This recipe does not install OpenStack yet. It declares the physical host
identity, BMC, provisioning NICs, base operating system profile, installer
assets, and validation evidence BKC should collect before any destructive PXE
or disk work is enabled.

The runnable version should reuse the same PXE and enrollment model used by the
Trixie smoke lane:

1. Register the physical server before an OS exists.
2. Validate BMC, DHCP/PXE, HTTP image assets, and checksums.
3. Install a supported base OS through one-shot PXE.
4. Enroll the host with BKC SSH and fact collection.
5. Prepare the Trixie host for OpenStack networking with the
   `trixie-openstack-host-prepare` lane.
6. Validate virtualization extensions, disks, NICs, time sync, and package
   baseline.
7. Hand off to the selected OpenStack installer provider.

Installer-specific choices such as Kolla Ansible, OpenStack Ansible, or another
provider belong behind provider actions. The node and evidence model should not
depend on one installer.

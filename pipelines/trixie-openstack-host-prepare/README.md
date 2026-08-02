# Trixie OpenStack Host Prepare

Post-enrollment preparation lane for a Debian Trixie host that will participate
in the OpenStack lab track.

This lane assumes the machine has already been PXE installed and enrolled with
BKC SSH. It prepares the operating system for OpenStack/Neutron without trying
to deploy the full OpenStack control plane.

The first implementation target is intentionally modest:

1. Install OpenStack client and Neutron host packages.
2. Install and enable Open vSwitch.
3. Apply kernel module and sysctl settings required by Neutron networking.
4. Write a local BKC host-prep marker with the chosen network mode.
5. Validate that packages, modules, sysctls, and OVS commands are present.

The Neutron agent service should stay disabled until the control plane, message
bus, credentials, and final ML2/OVS configuration are declared by the OpenStack
installer provider.

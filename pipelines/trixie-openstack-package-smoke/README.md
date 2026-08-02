# Trixie OpenStack Package Smoke

Lab-safe smoke lane for reusing an existing Debian Trixie VM, such as the
foo.bar SuiteCRM node or a clone of it, to test the OpenStack host preparation
package and configuration scripts.

This is not a hardware readiness test. It proves the repeatable install/config
path for Neutron/Open vSwitch packages, sysctl files, module-load files, and
BKC evidence markers. Kernel module and virtualization checks may be relaxed by
`BKC_OPENSTACK_SMOKE_MODE=true` because a reused VM may not expose everything a
real R630 host will expose.

Prefer a clone when time allows. Reusing `foobar-crm-01` is acceptable for a
quick package/config dry run, but it dirties the demo CRM node with OpenStack
packages.

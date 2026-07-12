# Bare Metal R630 PXE Validation

Validation and demonstration lane for the physical-first provisioning model.

This pipeline is intentionally non-destructive. It should prove that BKC can
reason about a powered-off physical server through declared hardware identity,
BMC reachability, NIC/MAC identity, DHCP/PXE boundaries, image assets, and
validation evidence before an operating system exists.

The later runnable version should collect real evidence from BMC/IPMI/Redfish,
DHCP leases, HTTP image checks, iPXE requests, installer callbacks, and first
boot enrollment.

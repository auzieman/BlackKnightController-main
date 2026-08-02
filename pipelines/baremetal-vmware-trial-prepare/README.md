# Bare Metal VMware Trial Prepare

Review-first preparation lane for validating the enterprise VMware-style track
on incoming R630-class hardware.

This recipe assumes VMware evaluation media, licenses, and entitlements are
supplied by the operator and are not committed to the repository. BKC should
only store sanitized node identity, boot intent, validation evidence, and
references to local image assets.

Current lab media reference:

- Source path:
  `/home/auzieman/Projects/blackknightcontroller-vmware/VMware-VMvisor-Installer-8.0U3e-24677879.x86_64.iso`
- Version label: `8.0U3e`
- Build: `24677879`
- SHA256:
  `9782c96ffd01cc56da17ec31573da69f4cba2f9402e67c8b55d05d9472c7376a`

The ISO itself remains outside the repository. The repository `.gitignore`
already excludes `*.iso`.

The runnable version should prove that BKC can:

1. Register a physical host and BMC before an OS exists.
2. Validate DHCP/PXE/HTTP installer delivery.
3. Render an ESXi-style unattended install profile from declared inputs.
4. Perform a one-shot PXE boot without causing install loops.
5. Validate management endpoint reachability after first boot.
6. Record datastore, NIC, API, and evaluation/license state evidence.
7. Optionally register the host with a trial vCenter or keep it standalone.

VMware-specific API and installer details belong behind provider actions so the
physical node lifecycle stays shared with Linux, Windows, Proxmox, and future
platforms.

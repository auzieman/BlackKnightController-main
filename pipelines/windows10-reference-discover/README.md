# Windows 10 Reference Discover

Review and preflight lane for the Windows 10 reference VM and installer ISO.

This lane does not rebuild Windows yet. It records the ISO source, validates
VMID 113 as a reachable reference target, verifies BKC OpenSSH key access, and
stages the firstboot and base-diet artifacts that will later be used by the
unattended install lane.

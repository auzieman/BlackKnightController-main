# Bare Metal Proxmox Trial Prepare

Server2 installs Proxmox VE using Proxmox's supported automated-installation
ISO tooling. Legacy iPXE loads Proxmox's original `linux26` and an initrd that
contains the complete, checksum-verified unattended ISO as `proxmox.iso`. This
keeps the install source visible after the kernel handoff and avoids modifying
Proxmox packages or installer logic. The PXE command line uses Proxmox 9.2's
own `proxmox-start-auto-installer` flag and exposes installer output on Dell
COM2 (`ttyS1`) through iDRAC SOL while retaining the local VGA console. Do not
add `proxtui` to the automatic lane; it selects the interactive TUI instead.

Current media: Proxmox VE 9.2-1, SHA-256
`4e88fe416df9b527624a175f24c9aa07c714d3332afb1ee3dbf3879573ef2c6c`.

Recording-ready auto-install media: SHA-256
`4146550e8a907175cd091f7bd9404e7d3655911d198e2b1b753e448ae3cdaa3b`.

Before power control is allowed, `validate-unattended-media` verifies the ISO
checksum and embedded answer file, requires the currently mounted BKC SSH key,
checks the declared `sda`/MAC/network intent, compares local and HTTP PXE asset
sizes, validates DHCP syntax and its disarmed one-shot fragment, and probes the
Server2 iDRAC Redfish system endpoint.

The install is deliberately protected by two controls:

1. `enable_pxe_arm=true` publishes a DHCP/iPXE route scoped to Server2 MAC
   `20:04:0f:e9:70:40`.
2. `enable_destructive_install=true` records approval for the destructive
   install plan. After preflight passes, the pipeline sets iDRAC PXE-once and
   performs the bounded cold boot itself.

Expected result: `r630-proxmox-01.lab.auzietek.com` at `10.20.0.41`, with the
Proxmox web/API endpoint on `https://10.20.0.41:8006/`.

Operational memory lives in `memory/fragments.json` and `memory/traces.jsonl`.
Read those files before changing this lane; they record the supported
auto-installer contract, one-shot PXE boundary, disk-boot validation, and the
known traps around repeated PXE boots.

The candidate owns the full one-shot lifecycle: verify the checksum-pinned
unattended ISO and embedded answer, require deny-by-default broad DHCP, arm only
Server2's exact LOM MAC, require fresh HTTP initrd evidence from `.241`, empty
the boot fragment, and wait until SSH, `pveversion`, KVM, storage, and the local
HTTPS API are all ready at the installed static address `.41`. The temporary
`.241` lease is installer-only and is intentionally not retained after handoff.

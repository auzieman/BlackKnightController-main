# Bare Metal OpenStack Lab Prepare

> Destructive filming lane: Debian Installer replaces the selected Server1 disk
> layout during normal unattended Partman processing. Disconnect temporary
> USB/SmartKMLink devices before the run; R630 firmware can stall during USB
> enumeration before PXE begins.

Live filming lane for rebuilding the first R630 as a Debian Trixie host and
installing the native single-node OpenStack lab services.

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

## First Host Intent

The first physical R630 is assigned to the Trixie/OpenStack track:

```text
node:physical_machine:r630-openstack-01
PXE MAC: 80:18:44:de:fd:38
temporary LAN/PXE address: 192.168.1.242
iDRAC/BMC observed address: 192.168.1.240
base OS: Debian Trixie
post enrollment: trixie-openstack-host-prepare
target management address: 192.168.1.242
target service address: 192.168.1.242
optional lab-control alias: 10.1.1.31, post pipeline only
storage profile: three ~1 TB mechanical disks
storage preference: pre-PXE PERC RAID5 for current capacity
storage fallback: RAID1 plus a single scratch/data disk
```

The iDRAC default credential was rotated during bring-up and should be
referenced only through `secret:bmc/r630-openstack-01/idrac`. Do not commit the
credential value.

## Storage Intent

For the first OpenStack host, prefer configuring the visible disks before PXE
through the hardware controller if iDRAC/PERC exposes that path cleanly. With
three approximately 1 TB mechanical disks, the practical current choices are
RAID5 for capacity or RAID1 plus a single scratch/data disk for simpler
recovery. RAID10 becomes the preferred VM-heavy layout once four or more disks
are available. If pre-PXE hardware RAID is not available, install Trixie to a
simple disk target and create the final storage layout after first boot with
normal Linux tooling.

Installer-specific choices such as Kolla Ansible, OpenStack Ansible, or another
provider belong behind provider actions. The node and evidence model should not
depend on one installer.

## Network Scope

For the current single-host lab, the LAN address remains the real UI, API, SSH,
PXE handoff, and BKC control path. The 10.1.1.x address is only an optional
post-enrollment alias for BKC/ns1 lab-control traffic. Separate service,
provider, and storage networks are deferred until the lab becomes multi-host or
adds dedicated storage.
The unattended preseed uses the proven Debian Partman and single-target GRUB
installer contract on the declared install disk. Destructive disk preparation
must not mutate other firmware-visible disks inside this installer profile;
controller layout and disk selection belong in a separate validated preflight.

## Known-good filming baseline

Run `447058a3-52df-4baa-a325-6254798b197e` completed on July 23, 2026:

- Server1 iDRAC/BMC: `10.20.0.119`
- PXE/install address: `10.20.0.240`
- PXE MAC: `80:18:44:de:fd:38`
- Installed hostname: `r630-openstack-01`
- Boot result: UEFI `Boot0004* debian`, `BootCurrent: 0004`
- Disk result: `/dev/sda` on PERC H730P Mini with EFI, ext4 root, and swap
- Enrollment: SSH as root and `/var/lib/bkc/base-provisioning.json`

Protect this baseline. The successful installer shape intentionally mirrors
`pipelines/ns1-trixie-pxe-smoke`:

- no `console=` kernel arguments in the filming lane;
- architecture-aware UEFI iPXE handoff from DHCP;
- `partman-auto/method string regular`;
- atomic Partman recipe with Debian's own confirmations;
- `tasksel tasksel/first multiselect standard, ssh-server`;
- `grub-installer/bootdev string /dev/sda`;
- no custom `grub-install`, no `method efi` detour, and no broad disk-wipe loop.

Evidence and firmware captures live in `memory/`. If this lane regresses, compare
against the known-good memory files and the smoke-lane templates before changing
installer semantics.

## Debian Installer contract

The base installation follows Debian Trixie's official automated-installation
appendix and example preseed:

- <https://www.debian.org/releases/trixie/amd64/apb.en.html>
- <https://www.debian.org/releases/trixie/example-preseed.txt>

Keep the installer lane narrow: pass initial network selection on the kernel
command line, let Partman own the declared `/dev/sda`, and let Debian's
`grub-installer` install to that same device. Do not duplicate `grub-install`.
This profile is destructive to the declared install disk only; do not wipe every
installer-visible disk because rescue USB media and temporary console devices
may be attached during filming. Hardware-controller normalization can remain a
separate concern.

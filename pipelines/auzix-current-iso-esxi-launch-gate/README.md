# AUZiX current ISO ESXi + repo launch gate

This lane is the current AUZiX “ship it only if it is a real duck” gate.

It wraps the r730 ISO build, ESXi boot smoke, and package launch checks in BKC
so the work is visible from the web UI and reviewable later during filming.

## Policy

- Heavy work runs on `r730-ai-01` / lab-build local disk.
- The laptop is only an operator/tunnel point.
- `ns1` can relay and publish, but should not be used as build scratch while the
  share is tight.
- Runtime audit warnings are acceptable for a private lab boot candidate only.
- Runtime audit warnings block public package repository and ISO payload publish.
- Package archives must preserve ownership, modes, setuid/sticky bits, scripts,
  and desktop launch contracts.
- A desktop menu entry is not valid unless its target installs and launches.
- ESXi must be keyed before ISO staging/boot smoke. Password bootstrap is only a
  setup step; repeated boots should use the checked-in SSH alias/key path.

## Current run evidence

The current successful lab candidate run is:

```text
run_id=current-live-20260816T185556Z
primary=/var/lib/auzix-build/published/auzix-live-installer-current-current-live-20260816T185556Z.iso
desktop=/var/lib/auzix-build/published/auzix-live-desktop-current-current-live-20260816T185556Z.iso
receipt=/var/lib/auzix-build/local-receipts/live-build-current-live-20260816T185556Z.receipt
```

Both ISOs passed the boot ISO publication contract on r730. They still need the
ESXi boot smoke and launch audit before any public payload promotion.

## 2026-08-16 ESXi boot-smoke receipts

- BKC run `4f6d5f36-e5d7-4ba1-9bd5-5268a5f55979` failed usefully: the pipeline
  stage executed, but the helper contract still expected `AUZIX_ISO_NAME` in
  the workstation/tunnel environment.
- BKC run `28dcf92d-89a9-46ca-bdc3-c367aa75a176` completed with a real
  executable `boot-desktop-iso-smoke` stage. It booted
  `auzix-live-desktop-current-current-live-20260816T185556Z.iso` on
  `auzix-esxi-workstation-media-01` and captured serial evidence in BKC.

The lane is intentionally mixed-mode for now:

- most stages record/review the gate contract;
- `boot-desktop-iso-smoke` executes the ESXi media attach, VM reboot, CD-ROM
  backing validation, and serial-tail capture.

Known remaining image-content warning from that run: the serial receipt says
`ssh tcp/22 not listening`. AUZiX commit `9688a01` records the investigation and
adds a bounded wait before the StartSequence SSH listen probe so the next ISO
can distinguish a real sshd failure from a too-early receipt.

## Semi-public package gate

Before package repo promotion, the validation target must prove the package can
install and run. The first launch set is:

- Terminology
- Midori
- Pluma / Gedit / Geany
- EPhoto
- LibreOffice Writer / Calc / Impress / Draw
- Flatpak runtime plus one GUI app
- Podman plus one AUZiX container and one normal upstream image

Failures should be captured from command output plus Enlightenment logs, then
folded back into the AUZiX package contracts. Do not keep patching the guest by
hand after the smallest useful evidence is captured.

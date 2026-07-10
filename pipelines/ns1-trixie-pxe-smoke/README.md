# ns1 Trixie PXE Smoke

This pipeline is the first Debian Trixie PXE smoke lane for the BKC node model.
It follows the ns1 provisioning network and DHCP preparation lanes.

The current executor wiring is an active smoke lane. It stages netboot assets
on ns1, renders iPXE and preseed files, recreates disposable VMID 132, requests
one PXE boot, flips the VM back to disk-first boot order, and finishes the
installed guest as a workstation profile.

- fetch and verify Debian Trixie netboot assets on ns1
- render iPXE and preseed/autoinstall assets
- create or reset disposable VMID 132 for PXE boot
- observe the first PXE boot handoff
- recollect final package and facter state after install
- install LibreOffice, MATE, Enlightenment, Terminology, developer tools, and
  VS Code
- enable graphical boot, SSH, and qemu-guest-agent services
- verify the workstation profile before recording relationships

Generated local credentials and the BKC OpenSSH key are added to the rendered
preseed at runtime. Generated credentials are written only to ns1 under
`/root`.

RustDesk is wired as an opt-in package URL in
`trixie-workstation-personalize`; the full PXE lane reuses that post-install
profile instead of guessing a moving upstream `.deb` filename.

Use `trixie-workstation-personalize` directly when iterating on the package
profile against an already installed VMID 132.

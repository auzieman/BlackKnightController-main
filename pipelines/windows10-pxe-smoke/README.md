# Windows 10 PXE Smoke

This pipeline is the first guarded Windows netboot lane for the BKC node model.
It uses ns1 as the isolated PXE/DHCP/HTTP controller and a disposable Proxmox
VM target.

The current milestone is a repeatable WinPE network boot that:

- stages Windows 10 install media on ns1 and serves it through Samba;
- injects `Autounattend.xml`, `winpeshl.ini`, `startnet.cmd`, and the BKC
  WinPE setup script through wimboot;
- maps `\\10.20.0.10\win10media` from WinPE;
- selects Windows 10 Pro with the generic KMS client setup key;
- wipes VMID 136 and installs to a single whole-disk `C:` partition;
- uses a SATA target disk for the smoke lane so stock Windows Setup can see
  the disk without VirtIO driver injection;
- flips the VM boot order back to disk-first after the one-shot PXE handoff;
- installs Chocolatey, LibreOffice, VS Code, and RustDesk after BKC regains
  SSH control of the installed OS;
- verifies the workstation profile before recording relationships.

Generated local admin credentials and the BKC OpenSSH key are staged for first
boot. The lane now targets a completed workstation state, while
`windows10-workstation-personalize` remains available for fast post-install
iteration against the current VM without rerunning WinPE.

Safety notes:

- VMID 113 remains the reference VM and is not rebuilt by this lane.
- VMID 136 is disposable for Windows PXE testing.
- The DHCP route is keyed to the fixed VMID 136 MAC address so the Debian PXE
  smoke lane keeps using its existing iPXE script.
- Generated credentials are written only to ns1 under `/root`.
- The pipeline swaps the served Windows iPXE entry to a local-disk guard after
  the first WinPE handoff so setup reboots do not reload WinPE.

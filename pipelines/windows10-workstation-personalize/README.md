# Windows 10 Workstation Personalize

Post-install personalization for VMID 136.

This pipeline deliberately does not rerun WinPE or Windows setup. It waits for
the installed Windows guest to be reachable over BKC SSH, then uses PowerShell
and Chocolatey to install workstation tools.

Initial profile:

- Chocolatey
- LibreOffice Fresh
- Visual Studio Code
- RustDesk

The target host can be changed in the dictionary if DNS does not yet resolve the
Windows management address.

# Windows 10 Workstation Personalize

Post-install personalization for VMID 136.

This pipeline deliberately does not rerun WinPE or Windows setup. It waits for
the installed Windows guest to be reachable over BKC SSH, then uses PowerShell
and Chocolatey to install workstation tools.

The PXE lane may generate a one-time bootstrap password and stage it as a
handoff artifact. This personalization lane normalizes the human-facing console
login from dictionary values. Current defaults are `depadmin` / `changeme123`.

Initial profile:

- Chocolatey
- LibreOffice Fresh
- Visual Studio Code
- RustDesk
- Google Chrome
- FooBar checkpoint file on the user's desktop

The target host can be changed in the dictionary if DNS does not yet resolve the
Windows management address.

Verification checks concrete installed application paths plus SSH service state
so a missing PATH alias or noisy Chocolatey output does not fail an otherwise
healthy workstation.

# Trixie Workstation Personalize

Post-install workstation personalization for VMID 132.

This pipeline deliberately does not recreate or PXE boot the VM. It assumes the
Debian Trixie smoke install has completed, then uses the Proxmox guest agent to
install the desktop and developer package profile.

The local demo console login is normalized during the run from dictionary
values. Current defaults are `auzieman` / `changeme123`.

Initial profile:

- LibreOffice
- MATE core desktop
- Enlightenment and Terminology
- Git/curl/wget/GPG tooling
- VS Code from the Microsoft apt repository when enabled
- RustDesk only when an explicit `.deb` URL is configured
- FooBar checkpoint file on the user's desktop
- Graphical target and LightDM enabled for NoVNC review

RustDesk is URL-gated because its Debian package artifact name changes by
release; the pipeline should not guess a moving download path.

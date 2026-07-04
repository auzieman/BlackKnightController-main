# AuziX VM134 Install Refresh

This lane is the replacement direction for the old VM130 surgical repair path.
VM130 remains useful for emergency SSH fixes, but VM134 is the better install
target because it has a larger disk and a cleaner Proxmox shape.

The lane currently proves the guarded prerequisites:

- AuziX source is at the expected commit.
- Installer shell/Lua contracts and GRUB packaging hooks exist.
- The staged strict root is refreshed with live tools, package tools, installer
  tools, finalizer, and GRUB.
- A VM134-specific ISO is built and published to Proxmox local ISO storage.
- VM134 has at least a 32 GiB target disk and boots ISO first, then disk.

The destructive disk install is deliberately left as a handoff until the live
TUI/GUI installer path is validated. Once that is stable, add a separate
install execution stage rather than mixing it into the preflight stages.

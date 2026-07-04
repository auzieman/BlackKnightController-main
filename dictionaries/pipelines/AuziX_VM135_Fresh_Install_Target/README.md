# AuziX VM135 Fresh Install Target

This lane treats VM135 as disposable. It consumes the latest published VM134 ISO
artifact, republishes it under a VM135 media name on Proxmox, recreates VM135
with a fresh 32 GiB disk, and boots ISO-first for installer validation.

Use this lane when the question is "does the current installer media work from
a clean target?" VM134 remains untouched as a comparison target.

The destructive install itself is still a handoff. Once the GUI/TUI installer is
stable, add a separate execution stage that runs the install against `/dev/sda`
and validates first disk boot.

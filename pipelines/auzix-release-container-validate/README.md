# AUZiX fresh release container validation

This stage produces three increasingly broad images from the prepared AUZiX
root and frozen package repository:

1. `auzix/service:zero-busybox-*` — canonical System substrate plus packaged BusyBox;
2. `auzix/service:one-nginx-*` — image zero plus nginx's frozen dependency closure;
3. `auzix/validation:pre-hdd-*` — the complete prepared root used by the HDD lane.

All build contexts and Docker layers are created on R730 local storage. The
laptop is only a Git/control/tunnel station. No dependency discovery or package
compilation occurs here: the pipeline assembles already prepared artifacts,
builds each Dockerfile, and validates the resulting running image.

The receipt requires the canonical glibc layout, ncurses/terminfo, Python SSL
and curses imports, Glances and htop under UID 1000, LibreOffice headless
document conversion, and resolvable package-provided desktop launchers.

The HDD lane remains locked until this stage writes a passing receipt.

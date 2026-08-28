# AUZiX fresh release container validation

This stage produces three increasingly broad images from an explicitly tagged
AUZiX source, a proved canonical base, and a frozen package repository:

1. `auzix/service:zero-busybox-*` — canonical System substrate plus packaged BusyBox;
2. `auzix/service:one-nginx-*` — image zero plus nginx's frozen dependency closure;
3. `auzix/validation:pre-hdd-*` — the complete prepared root used by the HDD lane.

Before creating a source snapshot or build context, preflight requires:

- a non-branch AUZiX source ref;
- valid release manifest hashes;
- canonical glibc loader, libc, and libgcc surfaces in the prepared base;
- a complete selected package closure, with only `Libc6` admitted as the
  external canonical base-provider identity;
- a `LibreOfficeWriter.runtime_ladder` exactly matching the recursive frozen
  repository closure after canonical runtime surfaces are excluded.

The last gate prevents an older leaf archive from surviving repository
consolidation after its dependency graph has changed. It runs before any
multi-gigabyte image assembly.

All build contexts and Docker layers are created on R730 local storage. The
laptop is only a Git/control/tunnel station. No dependency discovery or package
compilation occurs here: the pipeline assembles already prepared artifacts,
builds each Dockerfile, and validates the resulting running image.

The receipt requires the canonical glibc layout, ncurses/terminfo, Python SSL
and curses imports, Glances and htop under UID 1000, LibreOffice headless
document conversion, and resolvable package-provided desktop launchers.

The HDD lane remains locked until this stage writes a passing receipt.

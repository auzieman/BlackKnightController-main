# AUZiX Trixie package intake: real-duck validation fragment

Date: 2026-08-08

This fragment records the next gate for `auzix-trixie-package-intake`.

The package intake pipeline must not treat a package as desktop/workstation
ready just because the Debian intake built an archive and receipt. Before
promotion, install the candidate package into the AUZiX validation container and
prove it is a real runnable package.

Required evidence:

- install through `auzix-pkg` or the same archive/finalizer path used by vmid135;
- `stat` wrapper and resolved executable target;
- `file` wrapper and resolved executable target;
- `readelf -l` and `readelf -d` for ELF targets;
- `ldd` with the AUZiX/package library environment;
- fail on missing libraries, GLIBC/GLib symbol/version mismatches, bad
  interpreter, bad RPATH/RUNPATH, or unresolved app-specific shared objects;
- `strings` scan for hardwired donor paths;
- bounded `--version`, `--help`, or declared launch smoke;
- desktop packages must have a sensible `.desktop` entry whose `Exec=` points to
  an AUZiX-owned command path.

Suggested stage order after repository-build:

1. `repo-closure-audit`
2. `command-ldd-audit`
3. `desktop-readiness-audit`
4. `validation-container-install-smoke`
5. `ollama-runtime-review`
6. `repository-publish`
7. `repository-verify`

AUZiX-side source files:

- `packages/package-validation.contract.md`
- `scripts/audit-auzix-command-ldd.sh`
- `scripts/audit-auzix-desktop-readiness.sh`
- `scripts/audit-auzix-repo-runnability.sh`

Recent vmid135 failures that this fragment is meant to catch:

- `Galculator`: missing `libquadmath.so.0`.
- `File`: missing `libmagic.so.1`.
- `Curl`: `curl_global_trace` symbol mismatch.
- `Htop`, `Nano`, `Geany`, `Ripgrep`: GLIBC/GLib version mismatches.
- `CalligraWords`: missing `libkomain.so.40`.
- `LibreOffice`: BusyBox wrapper path/layout assumption.
- Multiple GUI packages: selectable in UI but dependency closure missing.

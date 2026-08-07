# AUZIX LAB 12 - Flatpak Strict Path Proof

This is the next BKC-visible lane after the Podman/container proof.

The point is not to build the whole desktop app world in one pass. The point is
to prove that Flatpak can enter AUZiX as an external application provider while
AUZiX keeps its own root contract:

```text
/Programs/<Name>/current/Commands
/System/Settings
/System/State
/Services
```

Donor paths such as `/usr`, `/lib`, `/bin`, `/etc`, `/var`, and `/tmp` are not
allowed to become base-image identity again. They can come back only as
break-fix compatibility exports after a failing proof names the exact path, the
owner package, why it is needed, and how we remove it later.

## First Proof Run

Cheap, focused default:

```bash
python3 -m json.tool packages/flatpak-desktop.queue.json >/dev/null
python3 -m json.tool packages/extended-ports.manifest.json >/dev/null
AUZIX_LEGACY_POLICY=invalid scripts/audit-auzix-strict-root.sh \
  out/auzix-strict/AuzixRoot \
  out/flatpak-strict-proof/audit-report.txt
```

If a current strict root exists and Docker is available, the stronger proof is:

```bash
AUZIX_PRUNED_IMAGE=auzix-strict:flatpak-proof \
  scripts/test-auzix-pruned-root.sh
```

That imports a copy of the root with compatibility symlinks removed and proves
the image starts without `/bin`, `/usr`, `/lib`, or `/lib64`.

## Package Order

The first Flatpak lane is deliberately small:

```text
Bubblewrap
OSTree
XdgDbusProxy
Flatpak
FlatpakFirefoxAdapter
```

`FlatpakFirefoxAdapter` is the shape we want for real app integration:

```text
/Programs/Firefox/current/Commands/firefox
  -> AUZiX adapter
  -> /Programs/Flatpak/current/Commands/flatpak
  -> /System/State/flatpak application store
```

The backing store may be weird and deduplicated. The user-facing command path
should still be normal AUZiX.

## ISO Gate

The 2026-08-08 stripped ISO run should start from no root-level legacy links.
Add links back only when the proof says which one broke and why.

That means the first ISO rebuild is a gate, not a rescue blanket:

- build stripped;
- boot or container-smoke;
- record the first exact failure;
- add the smallest `/System/Compatibility` break-fix export;
- rebuild and retest.

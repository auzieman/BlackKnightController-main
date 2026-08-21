# AUZiX 23 — VM Product Validation

This pipeline is the product gate: it proves the current AUZiX output in an
actual VM, not just inside an archive or build container.

It has two target levels:

1. live ISO review — boot the ISO, verify the live desktop/installer shape;
2. direct install and boot from disk — wipe the test VM, install to disk, remove
   ISO media, hard power-cycle, and verify first boot from the installed root.

The target VM is disposable. Fixes belong in AUZiX packages, installer logic, or
the build pipelines; the VM is evidence, not a hand-maintained snowflake.

Required proof points:

- keyboard and pointer work;
- LightDM/E session reaches the intended desktop;
- installer launches and can run the selected package set;
- `/System/Libraries`, `PATH`, `LD_LIBRARY_PATH`, permissions, setuid/sticky
  bits, groups, and package receipt state are sane;
- Midori or Firefox can open the AUZiX/Auzietek landing page;
- at least one terminal, one editor, and selected Office apps launch from menu
  and direct command;
- failures are captured from E logs, xsession logs, installer logs, and
  `auzix-pkg status`.


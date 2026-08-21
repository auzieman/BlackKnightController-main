# AUZiX 25 — Disk Install + Boot Validation

Take over the disposable AUZiX test VM from the live ISO, perform a real disk
install, detach the ISO, hard power-cycle, and validate boot from disk.

This lane is destructive to the target test VM. It is deliberately separate
from live ISO review so we can stop after live boot when the media itself is the
thing under inspection.


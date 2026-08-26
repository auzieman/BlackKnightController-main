# AUZiX release repository consolidation

This is the first stage of the recentered AUZiX release lane. It performs no
build and no dependency discovery.

It consumes the preserved 1,133-package Trixie spool and the earlier repository
that contains the reusable substrate/native packages. The dependency names
already recorded in the spool select the reusable subset. Every selected legacy
artifact must be Trixie, have a matching archive hash, and avoid a packaged
alternate glibc provider.

The output is a new immutable repository directory. Existing repositories,
spools, roots, images, and the known-good PVE media are read-only inputs.

The recursively closed reusable set is locked at 488 packages. Direct
dependencies alone are not a valid closure. One preserved receipt-backed
payload, `LibreOfficeCoreNogui`, is repacked as an explicit supplement before
consolidation. `Glances` is an explicit release root because leaf applications
cannot be discovered by walking dependencies. Any count drift stops the
pipeline.

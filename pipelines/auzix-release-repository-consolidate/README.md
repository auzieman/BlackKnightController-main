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

The expected reusable set is deliberately bounded at 300–350 packages. A count
outside that range is evidence of lineage drift and stops the pipeline.

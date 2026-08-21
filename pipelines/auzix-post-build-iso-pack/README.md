# AUZiX post-build ISO pack

This companion pipeline is intentionally separate from the long package/root
build. It should run only after `auzix-native-rebase-package-build` has a
container exit code of `0`.

The job packs the finished AUZiX root/repo artifacts into an ISO and publishes
the ISO receipt. This keeps the package factory and media factory from hiding
each other's failures.

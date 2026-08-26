# AUZiX fresh release container validation

This stage consumes only the immutable repository and manifest emitted by
`auzix-release-repository-consolidate`. It never copies or imports the mutable
package-build root.

The package payloads are materialized into an empty root with numeric ownership
preserved. Package receipts are recorded and declared post-install hooks are run
in dependency order from the frozen index.
The resulting root is imported as a disposable Docker image and checked as both
root and UID/GID 1000.

The HDD lane remains locked until this stage writes a passing receipt.

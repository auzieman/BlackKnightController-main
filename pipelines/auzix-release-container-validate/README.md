# AUZiX fresh release container validation

This stage consumes only the immutable repository and manifest emitted by
`auzix-release-repository-consolidate`. It never copies or imports the mutable
package-build root. It installs only the explicit dependency closure in
`selection/first-boot-cli.json`; the repository catalog is not an install
manifest. Preflight fails before extraction when a selected package or
transitive dependency is absent.

The package payloads are materialized into an empty root with numeric ownership
preserved. Package receipts are recorded and declared post-install hooks are run
in dependency order from the frozen index.
The resulting root is imported as a disposable Docker image and checked as both
root and UID/GID 1000.

The receipt requires the canonical glibc layout, ncurses/terminfo, Python SSL
and curses imports, Glances and htop under UID 1000, LibreOffice headless
document conversion, and resolvable package-provided desktop launchers.

The HDD lane remains locked until this stage writes a passing receipt.

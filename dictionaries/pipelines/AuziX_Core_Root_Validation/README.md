# AuZiX Core Root Validation

This pipeline is the cheap pre-ISO gate for AuZiX sustaining work.

It builds the strict root in the normal builder image, runs the root/package
audits, imports the root as a Docker image, runs a small CLI smoke test, and
copies these artifacts back to shared storage:

- `/srv/nfs/swarm/AuziX/src/out/core-validation/summary.json`
- `/srv/nfs/swarm/AuziX/src/out/core-validation/ollama-prompt.md`
- `/srv/nfs/swarm/AuziX/src/out/core-validation/strict-root-audit.txt`
- `/srv/nfs/swarm/AuziX/src/out/core-validation/package-runtime-audit.txt`
- `/srv/nfs/swarm/AuziX/src/out/core-validation/container-smoke.txt`

Use this before `auzix-vm134-install-refresh` or
`auzix-vm135-fresh-install-target` when changing core packages, startup,
permissions, browser/profile behavior, or graphical substrate packages.

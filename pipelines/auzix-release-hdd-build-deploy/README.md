# AUZiX validated HDD build and PVE deployment

This stage resolves a bounded desktop profile from the current baseline and
preserved native catalogs, creates a sparse image, and delegates disk/root
construction to the existing AUZiX package-profile installer. The bootstrap
root is an execution environment only; seed-runtime copying is disabled.

The selected closure must remain between 512 MiB and 2.5 GiB of compressed
archives. The expected desktop selection is about 640 packages / 1.2 GiB of
archives, producing the same general 1.5–2 GiB payload class as the previous
large desktop ISO.

VMID135 is the protected graphical reference. VMID142 is the default disposable
deployment target. Changing the target to 135 is rejected unless the explicit
protected-target override is enabled.

The pipeline records the image hash, PVE configuration, power-cycle result, and
network/SSH reachability. Desktop and launcher evidence remains a follow-on
product gate after the machine is reachable.

## Operator monitoring shells

Run these from the BKC repository in separate Terminology panes. The dashboard
distinguishes a running build, a receipted build, and a stopped build; a quiet
tail alone is not proof that a build is still running.

```sh
pipelines/auzix-release-hdd-build-deploy/scripts/monitor-hdd-build.sh desktop-main-20260828-r8 dashboard
pipelines/auzix-release-hdd-build-deploy/scripts/monitor-hdd-build.sh desktop-main-20260828-r8 log
pipelines/auzix-release-hdd-build-deploy/scripts/monitor-hdd-build.sh desktop-main-20260828-r8 progress
pipelines/auzix-release-hdd-build-deploy/scripts/monitor-hdd-build.sh desktop-main-20260828-r8 health
```

The first argument is always the immutable run ID. `AUZIX_LAB_HOST` defaults to
the remembered SSH alias `lab-ai-worker`.

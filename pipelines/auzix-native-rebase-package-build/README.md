# AUZiX native rebase package build

This pipeline runs the recentered AUZiX package factory from a committed git tag
on the R730/lab-build worker. It is intentionally split from ISO packing:

1. clone an exact AUZiX tag into a run directory;
2. rebuild the AUZiX builder image from that tag;
3. prove the builder has compiler, git, and apt candidate metadata;
4. start a named package/root/repo build container;
5. write a receipt with the container name and log command.

The build container runs detached so BKC does not have to babysit long package
builds. The receipt and container logs are the handoff.

This pipeline is also the anti-rabbit-hole rail: if a build fails, the next
change belongs in this pipeline or the AUZiX source/build contract, then the
pipeline is rerun from a new tag. Do not hand-repair the target root and call
that a build result.

Default AUZiX tag:

```text
auzix-alpha-base-lab-discovery-20260818-r2
```

Watch a run:

```bash
docker logs -f auzix-native-rebase-<run_id>
```

Stage runner:

```bash
scripts/run-native-rebase-package-build.sh preflight
scripts/run-native-rebase-package-build.sh clone
scripts/run-native-rebase-package-build.sh builder
scripts/run-native-rebase-package-build.sh smoke
scripts/run-native-rebase-package-build.sh start
scripts/run-native-rebase-package-build.sh status
```

After the package/root/repo build exits cleanly, use the companion
`auzix-post-build-iso-pack` pipeline to assemble the ISO from the finished
root.

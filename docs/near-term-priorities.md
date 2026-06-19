# BKC Near-Term Priorities

This file captures the practical direction after the recent lab sprint: Fedora clone work, COSMIC post-install, k3s, rx-demo, observability, NFS housekeeping, and BKC native SSH execution.

The goal is to spend limited implementation time on changes that reduce future friction.

## Priority 1: Action Catalog

Create `services/action_catalog.py` and a small file-backed catalog for reusable actions.

Initial actions should come from work that already succeeded in the lab:

- `ssh.probe`
- `ssh.install_bkc_key`
- `ssh.dnf_update`
- `ssh.reboot_and_wait`
- `ssh.nfs.ensure_mounts`
- `ssh.firewall.open_ports`
- `k3s.nodes.ready`
- `k3s.manifest.apply`
- `k3s.rollout.wait`
- `prometheus.scrape_job.ensure`
- `prometheus.targets.verify`
- `loki.stream.verify`
- `docker.service.force_update`
- `repo.git.status`

The first implementation can be conservative: metadata plus Python handlers for known action kinds. It does not need a full visual designer.

## Priority 2: Convert K3s Housekeeping To Actions

The k3s housekeeping pipeline is the best proving ground because it now includes several reusable operation types:

- verify cluster readiness
- ensure NFS mounts
- apply Kubernetes manifests
- open host firewall ports
- patch Prometheus config
- verify Prometheus target health
- verify Loki log streams
- keep loadgen running

When this pipeline is action-composed, the same actions can appear on kube1/kube2 resource pages.

## Priority 3: Lab Resync Pipeline

Add a post-reboot reconciliation pipeline.

Expected stages:

1. pull Proxmox inventory
2. scan/probe known SSH hosts
3. refresh Docker Swarm snapshot
4. refresh k3s node, pod, service, and namespace facts
5. verify Prometheus target state
6. verify key Loki streams
7. update resource graph facts and relationships

This should become the normal first button after power events or DHCP drift.

## Priority 4: Resource Page Actions

Resource detail pages should show actions that are valid for the selected resource kind and current permission scope.

Examples:

- host: probe, update packages, reboot and wait, install BKC key
- Kubernetes node: verify k3s, mount NFS, open telemetry ports, tail logs
- pipeline: run, retry failed, edit recipe, inspect source
- repository: fetch status, pull, show last commit, trigger build/deploy pipeline
- API: test connection, pull inventory, rotate credential note

Keep the UI simple at first: action list, required inputs, dry-run note, run button, recent result.

## Priority 5: Split The Executor By Responsibility

Do this after the action catalog has a foothold.

Proposed split:

- `services/pipeline_executor.py`: orchestration, run/stage state, dispatch
- `services/action_catalog.py`: action definitions and validation metadata
- `services/action_runner.py`: generic action invocation
- `services/actions/ssh.py`: SSH action handlers
- `services/actions/k3s.py`: k3s action handlers
- `services/actions/prometheus.py`: Prometheus action handlers
- `services/actions/docker.py`: Docker action handlers
- `services/actions/proxmox.py`: Proxmox action handlers

The split should follow real action reuse, not speculative architecture.

## Priority 6: Pipeline Recipe Files

Keep simple built-in pipeline definitions in Python for now, but prepare for repository-backed recipes.

Target locations:

- `pipelines/<pipeline-id>/pipeline.json` for built-in recipes
- `pipelines/<pipeline-id>/assets/` for small static inputs
- `pipelines/<pipeline-id>/templates/` for rendered scripts, manifests, and config
- `pipelines/<pipeline-id>/checks/` for validation helpers
- tenant override files under `dictionaries/tenants/<slug>/pipelines/`
- external Git repository references later

The UI should be able to edit metadata and simple stage inputs without hiding the source recipe from advanced users.

## Priority 7: Build And Deploy Preflight Gates

Add first-class resource gates before expensive or destructive stages.

AuziX lanes should fail early when the source commit, build workspace, package
repository, VM disk, or target filesystem is not in the expected state. The
recent 4 GiB VM target is the exact failure mode this should catch: storage
pressure can masquerade as package, permission, browser, or desktop regressions.

Initial gates:

- source ref or `.auzix-commit` matches the pipeline expectation
- build workspace has enough free space for the lane
- publish target is reachable and writable before repository publish
- target VM disk meets the lane minimum before install/deploy
- installed root exposes the expected finalizer, package receipts, and commands
- post-run validation checks user state, sudo/Xorg permissions, network, and GUI
  launch basics

## Defer For Now

- A full visual pipeline designer
- A complete plugin marketplace
- SNMP/network appliance deep discovery
- Complex credential vault integration
- Large UI rewrite before the resource/action model is stable

These are useful later, but they are not the next highest leverage step.

# Pipeline Cleanup And Examples

This note captures follow-up work from the recent demo pipelines and sets a
practical migration path for older lanes.

## Fixes From The Last Track

- Make run history the source of truth. Demo operations should run through BKC
  pipelines first, with direct shell reserved for repair and then backfilled into
  reusable actions.
- Replace placeholder commands in dictionary pipeline items with exact action
  inputs. Operator notes are useful, but runnable recipes need explicit targets,
  namespaces, image names, timeouts, and expected outputs.
- Add preflight gates before long stages. The recent failures were easier to
  diagnose after checking node readiness, registry reachability, source tags,
  DNS, and Loki/Promtail endpoint alignment.
- Keep source and runtime state separate. Git-tracked examples belong in
  `pipelines/`; lab-local runbooks, temporary endpoints, and credentials belong
  under mounted dictionaries.
- Record source refs in every deploy lane. Shared NFS working copies can drift,
  so a pipeline should show the Git ref, commit, and image tag it is using.
- Prefer narrow observability checks. Generate one known transaction and query
  the same Loki/Grafana endpoint the UI uses.
- Add cleanup/reset stages for demo lanes. A lane that creates VMs, containers,
  registry tags, or namespaces should document how to return to a rehearsal
  baseline.

## Examples Worth Promoting

The following examples are worth keeping in the main repository because they
show BKC's product shape without exposing lab secrets:

- `Demo: Swarm Image Registry`: deploy a simple registry, validate push/pull,
  and configure k3s trust.
- `Demo: K3s Add Node`: clone a worker, wait for SSH, join k3s, extend
  telemetry, and reset for another session.
- `Rx Demo K3s Deploy`: apply the k3s overlay, roll out the app and
  observability, then publish access links.
- `Rx Demo K3s Redeploy From Git`: record a commit-triggered deployment,
  build/push images, update k3s, and verify a CloudEvents audit transaction in
  Loki/Grafana.
- `Rx Demo K3s Undeploy`: capture state, remove app resources, and verify the
  registry remains intact.

These should be sanitized as examples by replacing hostnames, tokens, fixed IPs,
and local paths with variables or target selectors.

## Refactor Order

Migrate one lane at a time from dictionary-only notes to repository-backed
pipeline folders:

1. `rx-demo-k3s-redeploy-from-git`
2. `rx-demo-k3s-deploy`
3. `demo-k3s-add-node`
4. `demo-swarm-image-registry`
5. `rx-demo-k3s-undeploy`
6. AuziX VM lanes after the action catalog is stable

The redeploy lane is first because it exercises the full product loop: source
event, build, deploy, observability, and UI evidence.

## Folder Contract

Each promoted example should follow:

```text
pipelines/<pipeline-id>/
  README.md
  pipeline.json
  checks/
  templates/
  outputs.example.json
```

`pipeline.json` should use structured gates and stages, not only a list of
stage names. Stage entries should declare:

- `id`
- `action`
- `transport`
- `risk`
- `with`
- `produces`
- `timeout_seconds`

## Cleanup Actions To Add

The action catalog should gain these reusable actions from the last track:

- `git.source.sync`
- `docker.registry.probe`
- `docker.images.build_push`
- `k3s.nodes.ready`
- `k3s.node.provenance`
- `k3s.flannel.verify`
- `kubectl.manifest.apply`
- `kubectl.image.update`
- `kubectl.rollout.wait`
- `loki.ready`
- `loki.query.verify`
- `loki.purge_ephemeral`
- `promtail.endpoint.verify`
- `promtail.recycle`
- `grafana.datasource.verify`
- `rx_demo.transaction.generate`

These actions are small enough to test independently and broad enough to reuse
across demos.

## Definition Of Done For A Migrated Pipeline

- The repository folder has a structured `pipeline.json`.
- The mounted dictionary can point at or copy from that recipe.
- All destructive or long-running stages have gates.
- The pipeline produces useful run evidence: commit, target, image tag, endpoint
  URLs, and validation snippets.
- The README explains reset/cleanup and known failure modes.
- At least one example output file exists for UI/resource graph work.

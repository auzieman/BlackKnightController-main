# Rx Demo K3s Redeploy From Git

Repository-backed example for the demo lane that redeploys `rx-demo` from a Git
commit and validates the result through k3s, Prometheus, Loki, and Grafana.

This example intentionally avoids lab secrets. Real hostnames, credentials,
registry addresses, and kube targets should come from BKC integrations,
dictionary overrides, or run payloads.

## Flow

1. Record the triggering repository, ref, commit, and operator notes.
2. Sync a controlled source checkout to the requested commit.
3. Build and push commit-tagged images.
4. Update k3s deployments to the new image tag.
5. Wait for app rollout and verify pod networking.
6. Generate a known CloudEvents audit transaction.
7. Query the same Loki datasource Grafana uses.
8. Publish demo links and evidence into the run history.

## Lessons Captured

- The pipeline owns the main path; direct shell repairs are backfilled into
  actions.
- The source commit and image tag are recorded before any deployment work.
- Observability checks use one known transaction instead of broad dashboard
  queries.
- Loki validation checks the same endpoint Grafana reads.
- Node readiness and Promtail endpoint alignment are explicit gates.

## Reset

Use the undeploy lane if the application namespace needs to be removed. For a
rehearsal reset that keeps the app running, rerun this pipeline with a known
commit and generate a fresh transaction id.

# RX CANDIDATE — Deploy rx-demo Compose to ESXi Swarm

This lane is the Docker Swarm sibling of the earlier rx-demo k3s lanes.

The goal is not to rewrite rx-demo. The goal is to reuse its Docker Compose
shape against the ESXi-backed Swarm and compare deployment/runtime behavior with
the OpenStack-backed Swarm.

## Intended flow

1. Validate the ESXi swarm manager is reachable.
2. Validate `docker node ls` shows the intended shape:
   - two managers;
   - three workers;
   - app workloads constrained to `bkc.workload=app`.
3. Validate the rx-demo source checkout and Compose file exist.
4. Build/pull/preload images using the same registry rhythm as the k3s demo.
5. Render a Swarm-compatible stack file from the Compose source.
6. Deploy with `docker stack deploy`.
7. Validate service rollout, smoke URLs, and timing.
8. Record a performance/placement fragment for comparison.

## Current source boundary

The local `/home/auzieman/Projects/rx-demo` checkout is currently empty on this
laptop. Treat `rx_demo_source_path` as an explicit input supplied by Git, NFS, or
a restored working copy.

Do not invent rx-demo services in this repository. This pipeline should consume
the real rx-demo Compose file once the checkout is available.

## Why this matters

This is the clean demo point:

```text
same app intent
same BKC deploy rhythm
different substrate

k3s      -> Kubernetes API path
OpenStack -> Docker Swarm path
ESXi      -> Docker Swarm path
```

That lets BKC show the useful abstraction without pretending all substrates are
the same internally.

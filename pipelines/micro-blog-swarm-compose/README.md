# MICROBLOG CANDIDATE — Deploy Compose App to Docker Swarm

This lane is the “small canary” before the heavier `rx-demo` workload.

It proves the useful substrate path without making the first test carry SQL
Server, Tempo, load profiles, and the full RX workflow.

## Known-good intent

Use the real checkout at:

```text
/home/auzieman/Projects/micro-blog
```

The app has four locally built services:

- `blog-api`
- `blog-worker`
- `blog-projection`
- `blog-ui`

The app also uses stock images:

- `postgres:16-alpine`
- `rabbitmq:3.12-management`
- `redis:7-alpine`
- `otel/opentelemetry-collector-contrib:0.103.0`

## Deployment shape

Swarm should not consume the development Compose file directly.

The repeatable pattern is:

1. Validate the source checkout exists.
2. Validate the lab registry is reachable.
3. Validate the target Docker Swarm shape.
4. Build and push app images into the lab registry.
5. Render a Swarm stack file with `image:` references.
6. Stage `.env` from secrets/local operator input.
7. Deploy with `docker stack deploy`.
8. Validate service replicas and HTTP health.

## Optional: lab journal seed

Micro-blog can import markdown from its mounted `content/posts` tree. That makes
it a nice narrative canary: BKC can deploy the app and then seed a compact story
about what the lab just did.

Good inputs:

- cleaned BKC video/runbook notes;
- known-good pipeline fragments;
- short deployment timing summaries;
- generated “what changed” markdown from pipeline evidence.

Keep this optional. The app deployment should pass even if the narrative seed is
disabled.

## Optional: telemetry backhaul

There are two reasonable observability modes:

1. `main-lab-or-local`: expose the micro-blog OTEL collector metrics endpoint
   and let the existing lab monitoring scrape/query it.
2. `local-observability`: enable the app's local Grafana/Prometheus/Loki
   profile for a self-contained demo.

For the BlackKnight lab, the first mode is preferred for the quick canary: it
keeps the new swarm light and lets the main dashboards compare OpenStack, ESXi,
and the edge side in one place.

## Why not directly `docker stack deploy docker-compose.yml`?

The upstream Compose file is excellent for local development, but it contains
`build:` and `depends_on.condition` entries. Swarm does not build images during
stack deploy and does not use Compose health-gated dependency ordering.

So the known-good fragment is:

```text
compose source -> registry images -> rendered swarm stack -> rollout validation
```

## Registry note

Default lab registry:

```text
swarm1.lab.auzietek.com:5001
```

For hosts inside the isolated lab, use a registry name/IP that resolves from
the target swarm nodes. If DNS is not available, override `registry_host` with
the lab-reachable registry IP and port.

## Secret note

Do not put real OAuth/client secrets or production admin credentials into this
pipeline. Use `.env` on the target manager or BKC secret references.

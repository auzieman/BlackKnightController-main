# Rx Demo YouTube Walkthrough

## Opening

This demo shows a finished observability and deployment loop produced with
BlackKnightController, rx-demo, k3s, Grafana, Prometheus, Loki, Tempo, and
OpenTelemetry.

The point is not to run every setup step live. The point is to show the finished
good: repeatable pipelines, healthy instrumentation, dashboard evidence, and a
cleanup path that can reset the demo environment.

## What We Built

BlackKnightController now has visible demo lanes for the rx-demo story:

1. `Demo: Swarm Image Registry`
2. `Demo: Rx Demo K3s Deploy`
3. `Demo: Rx Demo Redeploy From Git`
4. `Demo: Rx Demo K3s Undeploy`

For the video, the live portion should focus on the rx-demo lanes. The image
registry and k3s storage/resize work are supporting infrastructure. They matter,
but they do not need to be performed live on camera.

## Supporting Work Already Done

Before the polished rx-demo path was possible, we had to make the lab reliable:

- Resize and storage cleanup work so k3s and build nodes had enough room to run
  images, logs, and telemetry without silent pressure.
- A local image registry path so k3s could pull demo images without depending
  on an external registry during recording.
- k3s host telemetry and Loki log collection so infrastructure behavior stayed
  visible while the app was being deployed.
- rx-demo observability wiring for metrics, traces, logs, and CloudEvents audit
  records.
- Grafana dashboard provisioning, including the Grafmaid panel plugin used by
  the live traffic maps.

These are not glamorous pieces, but they are the difference between a one-off
shell demo and a repeatable product demonstration.

## Why Pipelines Matter

The pipelines are the product surface. They make the demo explainable:

- A run has stages, status, timestamps, and evidence.
- Deploy and validation steps are repeatable instead of living in shell history.
- Failures land in one visible run ledger.
- The same run can publish links to Rx UI, Grafana, Prometheus, Loki, and Tempo.
- The cleanup path is explicit instead of being a risky manual teardown.

This is also where the collaboration with Codex helped: we used fast iteration
to turn live repairs into reusable pipeline behavior, then validated the result
with tests and rendered manifests.

## Time Saved

In a traditional development flow, this would usually be several separate work
streams:

- Kubernetes manifests and deployment scripts
- image build and registry automation
- telemetry instrumentation
- Grafana, Prometheus, Loki, and Tempo setup
- dashboard provisioning
- UI/API smoke checks
- deploy and cleanup runbooks
- documentation and demo preparation

A conservative estimate is that this could easily consume several days to a few
weeks of traditional engineering time, depending on how much troubleshooting was
needed. Here, we compressed the loop by working interactively: diagnose, patch,
validate, and immediately fold the fix back into the pipeline surface.

The important result is not just speed. The important result is that the demo
ended with reusable artifacts instead of a fragile recording script.

## Live Demo Flow

### 1. Show The Pipeline Console

Open:

```text
http://swarm1.lab.auzietek.com:5000/pipelines?tag=demo
```

Point out the four demo lanes:

- `Demo: Swarm Image Registry`
- `Demo: Rx Demo K3s Deploy`
- `Demo: Rx Demo Redeploy From Git`
- `Demo: Rx Demo K3s Undeploy`

Keep the focus on rx-demo. Mention that registry and storage work already made
the environment stable enough for this path.

### 2. Show Rx Demo Deploy Evidence

Open the latest `Demo: Rx Demo K3s Deploy` run.

Call out:

- k3s readiness
- runtime secrets check
- image build/push
- overlay apply
- app rollout
- observability rollout
- API and UI smoke checks
- telemetry check
- access links

The telemetry check now verifies more than process health. It checks that the
Grafmaid panel plugin is present in Grafana, which catches the failure mode
where dashboards exist but diagram panels cannot render.

### 3. Open The Running App And Dashboards

Open the published run links:

```text
Rx UI:      http://192.168.1.239:30080
Rx API:     http://192.168.1.239:30081
Grafana:    http://192.168.1.239:30300
Prometheus: http://192.168.1.239:30090
```

In Grafana, show:

- Rx traffic/service map
- Rx executive health
- Rx CloudEvents audit
- Tempo trace view, if traces are populated

The point to explain: metrics, logs, traces, and CloudEvents are not separate
demo tricks. They are different views of the same app behavior.

### 4. Run Or Show Redeploy From Git

Open `Demo: Rx Demo Redeploy From Git`.

Use this lane to explain the source-to-runtime story:

- record the Git event
- sync the source
- build and push images
- update k3s deployment images
- wait for rollout
- generate visible app activity
- verify CloudEvents audit records in Loki
- verify Grafana/Loki reachability

If running live feels risky, show a completed run and explain the stages. That is
still honest because the run ledger is the evidence.

### 5. Undeploy From The Last Demo Run

From the latest rx-demo deploy or redeploy run, use `Undeploy`.

This now queues the real cleanup lane:

```text
Demo: Rx Demo K3s Undeploy
```

Call out the cleanup stages:

- capture state
- delete demo observability resources
- delete the rx-demo namespace
- verify removal
- confirm the registry is retained

This is a good ending because it shows discipline: a demo environment should be
created, validated, shown, and reset through the same product surface.

## Instrumentation Point

The telemetry helpers should reduce missed fields, not hide intent.

The current rx-demo approach uses domain-specific calls such as API request,
queue message, database operation, cache operation, and error recording. That is
the right balance for this codebase: enough structure to make dashboards work,
but not a magical global logger that recursively parses arbitrary headers,
cookies, and tokens.

Good observability code should be boring:

- bounded labels
- known metric names
- explicit event types
- no recursive parsing of opaque payloads
- no high-cardinality surprise fields
- validation in dashboards and pipelines

## Closing

The finished good is not only rx-demo running on k3s. The finished good is the
loop around it:

1. deploy
2. validate
3. observe
4. redeploy
5. undeploy

That loop is now visible in BlackKnightController, backed by pipeline evidence,
and tied to real telemetry signals.

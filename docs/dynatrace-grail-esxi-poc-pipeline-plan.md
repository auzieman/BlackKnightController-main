# Dynatrace Grail ESXi POC pipeline plan

## Outcome

Build a repeatable, reviewable Dynatrace SaaS/Grail demonstration on the
existing ESXi lab without creating another one-off deployment path. Reuse the
current ESXi VM, K3s, rx-demo, tunnel, and BKC receipt machinery; replace only
the observability provider layer.

The plan is intentionally split into small pipelines. Each pipeline consumes
the previous pipeline's receipt and may be rerun independently.

## External prerequisites

- A valid Dynatrace SaaS POC environment and deployment tokens.
- Credentials are BKC secrets, never pipeline defaults or Git content.
- Outbound HTTPS from the lab to the SaaS environment.
- Existing ESXi host access and BKC lab routing are healthy.

## Pipeline sequence

### DT POC 00 — Preflight and inventory lock

- Run the normal lab preflight; do not invent a second power-control path.
- Inspect ESXi capacity, networks, datastores, templates, and existing K3s VMs.
- Resolve the SaaS environment URL and required secrets by reference only.
- Render a locked resource manifest with VM names, addresses, tags, sizing,
  software versions, and ownership label `bkc-poc=dynatrace-grail`.
- Refuse mutation when prerequisites or teardown ownership are ambiguous.

Output: immutable preflight and resource-manifest receipt.

### DT POC 10 — ESXi foundation

- Adapt the VM-provider stage from `vmware-k3s-lab-prepare`.
- Reuse existing guests when their receipt and configuration match; otherwise
  create only the missing tagged guests.
- Default shape: three small K3s nodes plus one ActiveGate/utility VM. Make CPU,
  RAM, disk, network, and template inputs data-driven.
- Validate boot, addressing, SSH, time, DNS, and outbound TLS before continuing.

Output: ESXi VM inventory and connectivity receipt.

### DT POC 20 — K3s platform

- Reuse the existing K3s bootstrap/template logic.
- Install or reconcile K3s idempotently from the locked VM inventory.
- Export kubeconfig into BKC-managed state and validate node readiness, DNS,
  storage class, ingress, and outbound HTTPS.

Output: cluster readiness receipt and kubeconfig reference.

### DT POC 30 — Dynatrace provider

- Install the Environment ActiveGate on the tagged utility VM.
- Install the Dynatrace Operator and render DynaKube configuration from BKC
  secrets and pipeline inputs.
- Enable Kubernetes, OneAgent, log, metric, and trace collection selected for
  the POC; validate agents and ActiveGate connectivity before workloads.
- Configure VMware monitoring through the ActiveGate using the ESXi/vCenter
  endpoint already present in inventory.
- Configure MSSQL monitoring only when its endpoint and credentials are present.

Output: provider deployment, health, and monitored-entity receipt.

### DT POC 40 — Workloads and telemetry

- Reuse `rx-demo-k3s-redeploy-from-git` for the Kubernetes demonstration.
- Reuse the ESXi compose proof only where a VM/container comparison adds value.
- Deploy the known MSSQL target or a tagged POC instance when requested.
- Validate application traffic plus logs, metrics, and traces before faults.

Output: workload endpoints and telemetry-baseline receipt.

### DT POC 50 — Demonstration and fault scenarios

- Run the existing rx-demo fault patterns as explicit, reversible stages.
- Record scenario start/end, affected workload, expected signal, observed
  Dynatrace problem/entity IDs, and recovery evidence.
- Optionally prepare a RustDesk endpoint on an existing workstation VM for the
  remote demonstration; it is not a dependency of the observability proof.

Output: human-readable demo sheet and machine-readable evidence receipt.

### DT POC 90 — Teardown or park

- Default to `park`: stop POC workloads and optionally power down tagged guests
  while retaining disks and receipts.
- `destroy` requires an explicit input and deletes only resources whose IDs and
  ownership tags appear in the locked manifest.
- Never delete untagged VMs, shared networks, templates, datastores, or existing
  ESXi/K3s infrastructure.
- Revoke temporary tokens separately and record that action without storing the
  token value.

Output: retained/deleted resource inventory and final cost/capacity summary.

## Implementation rules

- BKC pipelines are the execution authority; operator shell work is diagnostic
  and must be folded back into the responsible pipeline before rerun.
- Provider-specific operations are Jinja-rendered adapters fed by JSON inputs.
  The K3s, workload, evidence, and teardown stages remain provider-neutral.
- Pipeline definitions and dictionaries reload from committed filesystem state;
  rebuilding BKC is not a normal configuration step.
- Every mutating stage is idempotent and emits a receipt. Downstream stages use
  receipts, not rediscovery or guessed names.
- A failed stage stops its dependants. Troubleshooting never silently widens the
  resource manifest or installs an unplanned dependency.

## First implementation pass

1. Inventory the exact reusable steps in `vmware-k3s-lab-prepare`,
   `rx-demo-k3s-redeploy-from-git`, and the ESXi swarm pipelines.
2. Define the provider-neutral resource manifest and receipt schemas.
3. Implement pipelines 00, 10, and 20 using existing templates.
4. Add Dynatrace provider inputs only after the POC environment and tokens are
   available.
5. Prove park and guarded teardown before running the first fault scenario.

# vCenter Simulator Module Plan

This note captures the first candidate for the external
`BlackKnightController-modules` track: a vCenter connector backed by `vcsim`.

## Why This Module

`vcsim` from the VMware `govmomi` project gives us a lightweight vCenter and
ESXi API simulator. It is not a complete vCenter replacement, but it is enough
to build and test the first BKC connector behaviors before a real vCenter lab is
available.

The module lets BKC demonstrate a practical enterprise integration without
waiting on hardware, licensing, DNS, certificates, or a full vSphere deployment.

## Demo Shape

1. Start a `vcsim` container in the lab Docker Swarm or a local Docker context.
2. Register the simulator as a BKC vCenter integration.
3. Run a discovery pipeline.
4. Pull datacenter, cluster, host, datastore, network, folder, and VM inventory.
5. Map the discovered objects into BKC resources and relationships.
6. Run a basic VM action against the simulator, then refresh inventory.

The simulator endpoint should be treated like a normal vCenter endpoint:

```text
https://<host>:8989/sdk
```

For the demo, certificate validation can be explicitly disabled and called out
as lab-only behavior.

## Initial Connector Surface

The first module should stay narrow:

- connection profile: URL, username, password, insecure TLS flag
- health check: login/session creation and about/version details
- inventory discovery: datacenters, clusters, hosts, datastores, networks, VMs
- resource graph mapping: parent/child relationships and stable external ids
- sample action: power state read, power on, power off, or reset for one VM
- pipeline action ids: `vcenter.probe`, `vcenter.inventory.discover`,
  `vcenter.vm.power_state`, `vcenter.vm.power_on`, `vcenter.vm.power_off`

Avoid datastore mutation, clone workflows, DRS, tags, content libraries,
distributed switch operations, and vSphere namespaces in the first pass.

## BKC Integration Points

The BKC core should remain responsible for:

- storing integration profiles and encrypted credentials
- recording pipeline runs and stage events
- enforcing permissions
- rendering resource graph objects
- dispatching module actions through a stable action interface

The module should own:

- vCenter API client code
- vCenter object normalization
- simulator bootstrap helpers
- connector-specific validation checks
- example pipeline folders and test fixtures

## Lessons From The K3s/Rx-Demo Track

Recent demo work exposed a few module-design requirements:

- Pipeline runs must be the primary activity path; shell diagnostics should be
  captured as reusable BKC actions after they prove useful.
- Runtime state must be distinct from source state. A module can ship sample
  recipes, but lab-specific endpoints and credentials live in dictionaries.
- Shared working copies can drift from Git. Pipelines should record source refs
  and make preflight failures obvious.
- Observability should validate the same data path the UI uses. The Loki issue
  happened because one component wrote to Swarm Loki while Grafana queried k3s
  Loki.
- Preflight checks need to verify target readiness before expensive work. The
  kube3 `NotReady` state did not break the final Loki query, but it did create
  confusing rollout and terminating-pod noise.

## First Module Deliverable

The first shippable artifact in `BlackKnightController-modules` should be a
folder that can stand alone:

```text
modules/vcenter/
  README.md
  module.json
  actions.json
  pipelines/
    Demo_VCenter_Simulator/
      README.md
      pipeline.json
  src/
  tests/
```

The minimum useful demo is:

1. launch or verify `vcsim`
2. probe the endpoint
3. discover inventory
4. emit normalized resource graph facts
5. run one harmless VM action

## Open Questions

- Should the first client implementation use `pyvmomi`, `govc` subprocesses, or
  a small Go helper compiled into a container image?
- Should simulator lifecycle live in the module, or should it be a normal Docker
  pipeline supplied by BKC core?
- What exact module contract should BKC core load first: action definitions,
  pipeline folders, Python entry points, or all three?

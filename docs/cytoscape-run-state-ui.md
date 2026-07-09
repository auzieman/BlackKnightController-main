# Cytoscape Run State UI

BKC's current resource graph and pipeline console are server-rendered tables and
cards. That is useful for early development, but the Node model naturally wants
an interactive graph surface.

Cytoscape.js is a good candidate for that surface because it supports browser
graph visualization, compound nodes, layouts, event handling, and graph updates.
It can become a living version of the Mermaid diagrams BKC already wants to
generate.

## Recommendation

Do not start with a full graph editor.

Start with a read-only Cytoscape run-state view for one folder-backed pipeline,
then use that model to inform node and worker refactors.

The first useful target is the new provisioning path:

- `node:vm:ns1`
- `node:network:lab-mgmt`
- `node:router:spectrum-router`
- `node:network:lab-provisioning`
- `node:boot_image:debian-trixie-amd64-netboot`
- later `node:vm:vm132`

This graph can show open run state without requiring the user to infer progress
from a flat stage list.

## Why Start With Run State

Pipeline run state is already concrete:

- run id
- status
- ordered stages
- stage status
- events
- timestamps
- target resources
- action names
- produced facts

That is enough to render a graph without letting the browser mutate important
runtime state yet.

The UI can show:

- pipeline node
- stage nodes
- target node(s)
- produced/updated nodes
- evidence nodes
- failed/active/completed states

Example:

```text
Pipeline
  -> discover-current-network
  -> apply-provisioning-address
  -> validate-management-still-reachable

apply-provisioning-address
  targets ns1
  updates lab-provisioning
  produces fact:provisioning_address
```

## Proposed API Shape

Add a graph projection endpoint rather than exposing internal run JSON directly:

```text
GET /api/v1/runs/<run_id>/graph
```

Response:

```json
{
  "run_id": "abc123",
  "status": "running",
  "elements": {
    "nodes": [
      {
        "data": {
          "id": "run:abc123",
          "label": "ns1 provisioning network prepare",
          "type": "run",
          "status": "running"
        }
      },
      {
        "data": {
          "id": "stage:abc123:apply-provisioning-address",
          "parent": "run:abc123",
          "label": "apply-provisioning-address",
          "type": "stage",
          "status": "complete"
        }
      },
      {
        "data": {
          "id": "node:vm:ns1",
          "label": "ns1",
          "type": "vm",
          "status": "running"
        }
      }
    ],
    "edges": [
      {
        "data": {
          "id": "edge:stage-apply-target-ns1",
          "source": "stage:abc123:apply-provisioning-address",
          "target": "node:vm:ns1",
          "label": "targets"
        }
      }
    ]
  }
}
```

This keeps Cytoscape as a client of the BKC graph projection, not the source of
truth.

## Layout Rules

Use different layouts for different graph types:

- DAG pipeline flow: `dagre` or `breadthfirst`.
- Compound node groups: `fcose` or `cose-bilkent`.
- Small topology diagrams: `cose` or `fcose`.

Do not rely on one layout for every view. A pipeline flow, network topology, and
resource dependency graph have different visual goals.

## Interaction Model

Initial read-only interactions:

- click node: open side panel with facts, stage output, or resource details
- double click node: open existing BKC detail page
- filter by status: active, failed, complete
- fit to selected run
- highlight upstream/downstream relationships

Later editable interactions:

- create relationship draft
- attach node to group
- stage a dictionary override
- create a pipeline candidate from selected nodes
- save proposed graph edits as reviewable file diffs

The editing layer should create proposals, not immediately mutate production
runtime state.

## Worker Impact

Workers should not know about Cytoscape.

Workers should emit normalized run events and evidence:

```json
{
  "stage": "apply-provisioning-address",
  "status": "complete",
  "targets": ["node:vm:ns1"],
  "updates": ["node:network:lab-provisioning"],
  "produces": ["fact:provisioning_address"],
  "evidence": {
    "interface": "ens19",
    "address": "10.20.0.10/24"
  }
}
```

A server-side projector converts run events and node relationships into
Cytoscape elements.

## UI Placement

Add Cytoscape in this order:

1. Pipeline run detail: `Graph` tab for live run state.
2. Pipeline page: preview graph for next planned run.
3. Resource graph: topology/dependency view for selected node.
4. Node/procedure editor: proposal-based graph editing.

This order keeps the first implementation useful and low-risk.

## First Slice

For the ns1 provisioning path:

1. Add a graph projection helper that converts one run into Cytoscape elements.
2. Add a static/read-only graph panel to pipeline run detail.
3. Poll the endpoint every few seconds while a run is active.
4. Color nodes by status.
5. Link target nodes back to `/resources`.

That gives BKC a living operational diagram without blocking the Node model,
dictionary scoping, or worker refactor.

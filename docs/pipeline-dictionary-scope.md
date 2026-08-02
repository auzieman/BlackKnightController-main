# Pipeline Dictionary Scope

BKC currently has two related ideas that should be brought closer together:

- repository-backed pipeline recipes under `pipelines/<pipeline-id>/`
- runtime/lab dictionaries under `dictionaries/`

The long-term shape should let a portable pipeline recipe be reused in multiple
labs while each lab, tenant, group, or run can provide lower-scoped values
without editing the reusable recipe.

## Goals

- Keep portable pipeline logic in repository-backed pipeline folders.
- Keep lab-local values in mounted dictionary storage.
- Allow a pipeline to declare default variables.
- Allow runtime dictionaries to override those defaults by scope.
- Make variable resolution visible in run evidence.
- Avoid one large global dictionary becoming the hidden source of truth.

## Recommended Layout

Repository-backed recipe:

```text
pipelines/<pipeline-id>/
  README.md
  pipeline.json
  defaults.json
  assets/
  checks/
  templates/
  outputs.example.json
```

Runtime/lab override:

```text
dictionaries/pipelines/<Pipeline_Name>/
  README.md
  pipeline.json
  dictionary.json
  nodes.json
  relationships.json
  items/
    00-preflight.json
    10-build.json
    20-validate.json
```

Tenant or environment override:

```text
dictionaries/tenants/<tenant>/
  dictionary.json
  integrations.json
  pipelines/<Pipeline_Name>/
    dictionary.json
```

This lets BKC keep the portable pipeline in Git and the lab-specific values in
the mounted runtime dictionary volume.

## Variable Resolution

Variables should resolve from broadest to narrowest scope:

1. Built-in safe defaults.
2. Repository pipeline `defaults.json`.
3. Tenant dictionary.
4. Tenant pipeline dictionary.
5. Runtime pipeline dictionary.
6. Group or cluster dictionary.
7. Target node facts and labels.
8. Run request inputs.
9. Stage-local variables.

Later scopes override earlier scopes.

The executor should record the resolved variable map or a redacted summary in
run evidence. Secret values should be represented by secret references, not raw
values.

## Reusing Pipelines

A reusable pipeline should avoid hardcoded lab values. It should select targets
through nodes, relationships, labels, or variables.

Portable recipe example:

```json
{
  "id": "rx-demo-k3s-redeploy-from-git",
  "variables": {
    "namespace": "rx-demo",
    "registry_repo": "${dictionary.registry_repo}",
    "kubernetes_cluster": "${target.cluster}"
  },
  "target_selector": {
    "type": "cluster",
    "labels": {
      "role": "k3s-demo"
    }
  }
}
```

Runtime override example:

```json
{
  "registry_repo": "swarm1.lab.auzietek.com:5001/rx-demo",
  "grafana_url": "http://swarm1.lab.auzietek.com:3000",
  "kubernetes_context": "kube1"
}
```

The same pipeline recipe can then run against another lab by changing the scoped
dictionary instead of copying or rewriting the pipeline.

## Composed Pipelines

Higher-level recipes should call lower-level recipes by intent rather than
copying their stages. This matters for the hardware migration path:

```text
baremetal-vmware-trial-prepare
  -> produces ESXi host facts
  -> records platform:vmware capacity

vmware-k3s-lab-prepare
  -> consumes platform:vmware capacity
  -> reuses rx-demo/k3s deployment dictionary values
  -> creates or selects k3s VM nodes
  -> invokes the existing k3s deployment lane
```

The composed recipe owns target selection and variable binding. The reused
recipe owns the actual k3s deployment logic. That keeps BKC from forking a
nearly identical Kubernetes lane for Proxmox, VMware, OpenStack, or bare metal.

## Pipeline Values As Nodes

As the Node model matures, pipeline dictionaries should refer to nodes rather
than raw strings where possible.

Prefer:

```json
{
  "registry_node_id": "node:service:lab-registry",
  "cluster_node_id": "node:cluster:k3s-lab"
}
```

Over:

```json
{
  "registry_host": "swarm1.lab.auzietek.com",
  "cluster_host": "kube1.lab.auzietek.com"
}
```

Raw values are still useful for bootstrap and external endpoints, but node
references give BKC a chance to derive URLs, credentials, network paths, and
impact relationships from the graph.

## Stage-Scoped Dictionaries

Stage-local values should live close to the stage when they are only meaningful
there.

```json
{
  "id": "registry-health",
  "action": "http.check",
  "dictionary": {
    "expected_status": 200,
    "timeout_seconds": 10
  }
}
```

This keeps a pipeline reusable without promoting every small setting into a
global dictionary.

## Migration Rule

Do not move every dictionary at once.

For each pipeline:

1. Identify hardcoded lab values.
2. Move portable defaults into `pipelines/<pipeline-id>/defaults.json`.
3. Move lab values into `dictionaries/pipelines/<Pipeline_Name>/dictionary.json`.
4. Update the pipeline to reference variables or node IDs.
5. Record resolved variables in run evidence.
6. Keep legacy dictionary keys available until the pipeline has passed a clean
   run using the scoped layout.

The first good candidates are demo pipelines because they already have clear
lab-local values and visible validation output.

Folder-backed pipelines should also expose enough resolved state for graph
projection. See [`cytoscape-run-state-ui.md`](cytoscape-run-state-ui.md) for the
proposed read-only run-state graph view.

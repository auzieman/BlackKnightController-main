# BKC Pipeline And Action Model

BKC pipelines are not intended to become monolithic Python scripts. A pipeline is a structured recipe that composes reusable actions, templates, target selectors, credentials, checks, and produced facts into a tracked run.

The pipeline executor should interpret that recipe. It should not become the only place where operational knowledge lives.

## Core Terms

| Term | Meaning |
|------|---------|
| Resource | A thing BKC knows about: host, VM, API, repository, storage share, service, credential scope, action, pipeline, or capability block. |
| Action | A reusable operation definition with inputs, target rules, execution mode, risk level, expected outputs, and validation checks. |
| Action template | A parameterized script, command, manifest, API request, or render step used by an action. |
| Pipeline | An ordered recipe of actions and gates. It records a run, stages, events, outputs, and produced inventory facts. |
| Capability block | A functional stack such as k3s, monitoring, Docker Swarm, DNS/DHCP, or an application deployment. |
| Produced fact | Durable knowledge learned from a run, such as mounted NFS paths, a k3s role, a Prometheus scrape target, or a service URL. |

## Design Principles

1. Pipelines compose actions.
2. Actions own reusable operational logic.
3. Templates carry target-side scripts, manifests, or config fragments.
4. The executor resolves targets, renders inputs, runs actions, validates outputs, and records facts.
5. Inventory and relationships should improve after successful runs.
6. Raw command output is useful for debugging, but durable facts should be promoted into the graph.
7. A failed stage should identify the resource, action, target, and validation that failed.

## Pipeline Recipe Shape

The long-term shape should be JSON/YAML friendly so the UI can edit simple cases and advanced users can keep recipes in Git.
Repository-backed recipes should live under
`pipelines/<pipeline-id>/pipeline.json` with sibling folders for assets,
templates, checks, examples, and operator notes.

```json
{
  "id": "k3s-lab-housekeeping",
  "name": "K3s Lab Housekeeping",
  "targets": {
    "cluster": "resource:k3s-lab",
    "nodes": "query:kind=kubernetes-node tag=lab"
  },
  "stages": [
    {
      "id": "verify-k3s",
      "action": "k3s.nodes.ready",
      "with": {
        "timeout_seconds": 90
      }
    },
    {
      "id": "nfs-projects",
      "action": "ssh.nfs.ensure_mounts",
      "target": "nodes",
      "with": {
        "mounts": [
          {
            "source": "192.168.1.10:/srv/nfs/swarm/shared",
            "target": "/mnt/swarm/shared",
            "fstype": "nfs4"
          }
        ]
      },
      "produces": [
        "relationship:uses_storage"
      ]
    },
    {
      "id": "apply-loki-logs",
      "action": "k3s.manifest.apply",
      "with": {
        "template": "k3s-loki-logs.yaml",
        "namespace": "rx-observability",
        "rollout": "ds/promtail-k3s"
      },
      "produces": [
        "relationship:ships_logs_to"
      ]
    }
  ]
}
```

This is a target shape, not a requirement that every current pipeline must migrate immediately.

## Action Shape

Actions should be small enough to reuse directly from a resource page and inside a pipeline.

```json
{
  "id": "ssh.nfs.ensure_mounts",
  "label": "Ensure NFS mounts",
  "kind": "ssh",
  "target_kinds": ["host", "kubernetes-node"],
  "credential_scope": "ssh:admin",
  "risk": "medium",
  "inputs": {
    "mounts": "list",
    "install_package": "bool"
  },
  "templates": [
    "actions/ssh/ensure-nfs-mounts.sh.j2"
  ],
  "validations": [
    "findmnt exact mountpoint",
    "fstype is nfs or nfs4"
  ],
  "produces": [
    "fact:nfs_mounts",
    "relationship:uses_storage"
  ]
}
```

## Execution Modes

BKC should support several action modes under one catalog:

- **SSH command**: direct command or rendered shell script sent to a target.
- **SSH rendered template**: render server-side, copy to target, execute or install.
- **API call**: Proxmox, Docker, BKC API, vendor API, or future plugin.
- **Kubernetes manifest**: render/apply manifest, wait for rollout, verify state.
- **Local/controller action**: run on BKC, a worker, or a configured controller host.
- **Manual checkpoint**: pause for an operator confirmation or external step.

Ansible remains useful, but BKC native SSH actions should cover simple operations without requiring operators to learn Ansible first.

## What Should Not Happen

- Do not keep adding large workflow-specific function clusters to `pipeline_executor.py`.
- Do not hard-code lab IPs inside reusable action logic when they can be pipeline inputs or inventory facts.
- Do not treat raw command success as enough when a stronger validation is available.
- Do not run image builds, repository publishes, or VM installs without explicit source, workspace, publish-target, and target-disk gates.
- Do not create arbitrary relationships without a typed relationship rule.
- Do not copy secrets into action or pipeline definitions; reference credential scopes.

## Migration Path

1. Add an action catalog service.
2. Define the first reusable actions from already-proven lab work.
3. Teach the pipeline executor to run catalog actions.
4. Convert one pipeline at a time, starting with k3s lab housekeeping.
5. Add resource-page action buttons after the same action definitions can run outside a pipeline.
6. Promote successful action outputs into resource facts and relationships.

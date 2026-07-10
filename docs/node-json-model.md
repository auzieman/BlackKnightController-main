# BKC Node JSON Model

This is the first-pass contract for the PowerCockpit-style BKC model. It is
intended to frame the current inventory, resource graph, integration, and
pipeline data without forcing a full rewrite.

## Core Rule

Everything BKC can reason about is a Node.

A Node may be actionable, structural, or both:

- Actionable nodes can be targeted by procedures, discovery, validation, or
  integrations.
- Structural nodes provide context for impact analysis, diagrams, ownership,
  topology, or placement.
- Some nodes, such as switches, firewalls, hypervisors, and Kubernetes clusters,
  are both actionable and structural.

Node type defines expected fact shape, supported relationships, display
sections, and available procedures.

## Base Node

Every node should support these fields:

```json
{
  "id": "node:vm:kube1",
  "name": "kube1",
  "type": "vm",
  "display_name": "kube1",
  "description": "",
  "actionable": true,
  "structural": false,
  "lifecycle_state": "managed",
  "current_state": "running",
  "desired_state": "running",
  "tags": ["k3s", "lab"],
  "labels": {
    "environment": "lab"
  },
  "facts": {},
  "owners": [],
  "security_classification": "internal",
  "source": {
    "system": "bkc",
    "method": "declared"
  }
}
```

Recommended ID format:

```text
node:<type>:<stable-name-or-provider-id>
```

The ID should be stable enough for relationships and run history. Display names
can change more freely.

## Initial Node Types

Infrastructure:

- `bare_metal`
- `vm`
- `container`
- `kubernetes_node`
- `kubernetes_pod`
- `network_switch`
- `firewall`
- `router`
- `storage`
- `network`
- `location`
- `rack`

Application and platform:

- `service`
- `database`
- `application`
- `repository`
- `pipeline`
- `procedure`
- `boot_image`

These are not meant to be exhaustive. New node types should add typed
expectations without breaking the base contract.

## Type Profiles

A type profile describes how BKC should treat a node type.

```json
{
  "type": "network_switch",
  "label": "Network Switch",
  "actionable_default": true,
  "structural_default": true,
  "fact_sections": ["identity", "management", "hardware", "ports", "vlans"],
  "relationship_hints": ["located_in", "connects_to", "provides_network"],
  "procedure_hints": ["discover_switch", "backup_config", "validate_ports"]
}
```

The UI can use type profiles to render predictable panels without hardcoding
every node shape into each page.

## Facts

Facts are typed observations about a node. Facts should be namespaced by the
domain that produced them:

```json
{
  "facts": {
    "identity": {
      "hostname": "kube1",
      "fqdn": "kube1.lab.example"
    },
    "network": {
      "primary_ip": "192.168.1.14",
      "observed_ips": ["192.168.1.14", "10.20.0.14"]
    },
    "proxmox": {
      "vmid": 101,
      "host": "pve1"
    },
    "os": {
      "name": "Fedora",
      "version": "40"
    }
  }
}
```

Facts should eventually carry observation metadata. The initial JSON shape can
keep that lightweight:

```json
{
  "observed_at": "2026-07-07T00:00:00Z",
  "source": "ssh:facter",
  "confidence": "observed"
}
```

## Groups And Clusters

Groups and clusters are nodes, but membership should be represented as
relationships rather than embedded lists only.

- A group is a logical management target.
- A cluster is a coordinated runtime or infrastructure boundary.
- A node can belong to multiple groups and clusters.

Example:

```json
{
  "source_id": "node:group:rx-demo",
  "type": "contains",
  "target_id": "node:service:rx-ui"
}
```

## Relationships

Relationships are first-class graph edges.

Required fields:

```json
{
  "source_id": "node:vm:kube1",
  "type": "runs_on",
  "target_id": "node:bare_metal:pve1",
  "source": "proxmox-discovery",
  "confidence": "observed"
}
```

Initial relationship types:

- `contains`
- `member_of`
- `runs_on`
- `hosts`
- `depends_on`
- `uses`
- `connects_to`
- `provides_network`
- `located_in`
- `observed_by`
- `deploys`
- `targets`
- `owned_by`
- `supports_business_function`
- `provides_dhcp`
- `serves_pxe`
- `booted_from`
- `supersedes`
- `ignore_updates`

Prefer directional relationships with clear meaning. The UI can infer reverse
labels when needed.

For topology rendering, prefer concrete operational edges over name inference:

```json
{
  "source_id": "node:service:ns1-dhcpd",
  "type": "provides_dhcp",
  "target_id": "node:network:lab-provisioning",
  "source": "bkc",
  "confidence": "declared"
}
```

This lets the graph render `ns1 -> dhcpd -> lab-provisioning` without guessing
from host names or pipeline stage labels.

## Networks And Boot Images

Provisioning needs network and boot image objects early, so they are modeled as
nodes from the start.

Network nodes should capture:

- CIDR
- gateway
- DNS
- DHCP range
- bridge/interface hints
- provisioning role
- PXE/iPXE settings

Boot image nodes should capture:

- distribution
- version
- architecture
- kernel path
- initrd path
- source URL
- checksum
- local cache path

This lets DHCP, PXE, VM creation, and bare metal provisioning procedures consume
the same graph model instead of inventing a separate provisioning schema.

## Update Policy

Nodes can declare how BKC should treat updates from pipelines, scans, and human
edits. This is especially important for shared infrastructure such as DHCP,
PXE, and boot media directories.

Use `facts.update_policy` for node-local rules:

```json
{
  "facts": {
    "update_policy": {
      "mode": "managed-fragment-only",
      "ignore_paths": ["/etc/dhcp/dhcpd.conf"],
      "managed_paths": ["/etc/dhcp/dhcpd.d/bkc-provisioning.conf"],
      "reason": "BKC owns the include fragment, not the operator-owned base file."
    }
  }
}
```

Suggested modes:

- `authoritative`: BKC owns the whole object.
- `managed-fragment-only`: BKC owns only declared paths or subdocuments.
- `additive`: BKC may add files or relationships but should not prune unknowns.
- `ephemeral-rebuild`: the node can be destroyed/recreated; volatile fields
  should not block matching.
- `review-required`: writes require an explicit review or approval gate.

For graph-level exceptions, use an explicit relationship:

```json
{
  "source_id": "node:pipeline:ns1-trixie-pxe-smoke",
  "type": "ignore_updates",
  "target_id": "node:field:target_user_password",
  "source": "bkc",
  "confidence": "declared"
}
```

The relationship form is useful when an update ignore is scoped to one
procedure, one integration, or one volatile field rather than the node as a
whole.

## PXE And DHCP Guardrails

DHCP and PXE should be modeled as services with clear authority boundaries:

- management network DHCP can remain externally owned
  (`bkc_managed: false`, authority `spectrum-router`)
- provisioning DHCP can be BKC-managed but disabled until reviewed
- BKC should prefer include fragments such as
  `/etc/dhcp/dhcpd.d/bkc-provisioning.conf`
- PXE assets should be additive, preserving operator-owned rescue media
- target VMs should record one-shot boot intent and post-install boot order

Example relationship chain:

```text
node:vm:ns1
  <- runs_on <- node:service:ns1-dhcpd
  <- runs_on <- node:service:ns1-pxe

node:service:ns1-dhcpd -> provides_dhcp -> node:network:lab-provisioning
node:service:ns1-pxe   -> serves_pxe    -> node:network:lab-provisioning
node:vm:trixie-smoke-132 -> booted_from -> node:boot_image:debian-trixie-amd64-netboot
```

The Cytoscape/resource graph should eventually consume these relationships
directly instead of relying on label-based placement heuristics.

## Procedures

Procedures are graph-aware automation definitions.

A procedure should:

- consume one or more nodes
- create or update nodes
- create or update relationships
- emit run evidence
- refresh facts when practical

Pipeline definitions can evolve into procedure templates. Pipeline runs can
appear as operational nodes or run records linked to the procedure and targets.

## Migration From Current BKC Concepts

Current concept to node model:

- inventory host -> `node:bare_metal` or `node:vm`
- Proxmox VM -> `node:vm`
- Docker container/service -> `node:container` or `node:service`
- Kubernetes node -> `node:kubernetes_node`
- Kubernetes pod -> `node:kubernetes_pod`
- API integration -> actionable service or observer node
- pipeline definition -> `node:procedure` or `node:pipeline`
- pipeline run -> run evidence linked to target nodes
- group vars group -> `node:group`
- resource graph resource -> normalized node view

The first implementation should adapt existing resource graph output into this
shape before replacing storage internals.

Interactive graph UI work should consume this model through projections rather
than becoming a second source of truth. See
[`cytoscape-run-state-ui.md`](cytoscape-run-state-ui.md) for the first proposed
read-only run-state graph slice.

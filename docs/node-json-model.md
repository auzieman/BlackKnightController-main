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

Prefer directional relationships with clear meaning. The UI can infer reverse
labels when needed.

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

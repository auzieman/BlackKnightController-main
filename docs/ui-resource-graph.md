# BKC Resource Graph UI

BKC should present the lab as a graph of resources, not as a machine-only inventory. A resource can be a VM, physical host, container, storage pool, Git repository, API interface, credential boundary, pipeline, action, template, service, or external integration.

The UI should borrow the useful shape of Proxmox-style tools without copying their object model:

- persistent left resource tree
- main object detail pane
- top tabs for major aspects of the selected object
- contextual actions based on resource type and permissions
- task/history visibility near the object being changed

Free icon sets such as Lucide, Tabler, Bootstrap Icons, or Font Awesome can give quick visual separation between resource kinds. Icons should clarify type, not become the primary data model. Every icon needs text, state, and relationship context beside it.

## Resource Kinds

The resource graph should start broad enough for lab reality:

| Kind | Examples | Primary Tabs |
|------|----------|--------------|
| Compute | VM, physical host, container, LXC | Summary, Relationships, Actions, Inventory, Storage, Logs |
| Appliance | router, switch, firewall, NAS, SAN, UPS, PDU | Summary, Interfaces, Credentials, Actions, History |
| API | Proxmox, Docker manager, BKC, vendor controller | Summary, Inventory, Credentials, Actions, Logs |
| Storage | local-lvm, NFS share, NAS volume, object bucket | Summary, Capacity, Relationships, Actions, History |
| Network | bridge, VLAN, switch port, subnet, DNS/DHCP zone | Summary, Relationships, Inventory, Actions |
| Git | repository, branch, release tag, deployment source | Summary, Relationships, Actions, History |
| Credential | SSH key, API token, password set, vault reference | Summary, Scope, Relationships, Rotation History |
| Action | SSH command, template render, inventory sync, clone, deploy | Summary, Targets, Inputs, History |
| Pipeline | ordered action graph, deploy lane, install lane | Summary, Stages, Relationships, Runs, Logs |
| Capability Block | monitoring, web app, database, build worker, storage service | Summary, Components, Actions, Health |

Icon examples:

- server/box for compute
- router/network for appliances and network
- key/lock for credentials
- git-branch for repositories
- plug/cloud for APIs
- database/hard-drive for storage
- play/list-check for actions and pipelines
- boxes/layers for capability blocks

## Resource Tree

The left tree is a navigation and relationship surface. It should support mixed resource types:

```text
Lab
  Proxmox
    pve
      VMs
      Storage
  Git
    BlackKnightController
    tabor-linux-forge
  APIs
    Proxmox API
    BKC API
    Docker manager API
  Groups
    swarm
    monitoring
  Pipelines
    Fedora template deploy
    Monitoring stack
  Actions
    SSH command sets
    Kickstart staging
    Inventory sync
```

Tree nodes need a `kind`, display name, health/state summary, and relationship hints. They do not need to be machines.

## Operating Sequence

The UI should support a natural progression from discovery to repeatable operations:

1. **Add an API or integration**
   - Add endpoint, auth mode, TLS behavior, and credential reference.
   - Test connection.
   - Pull raw inventory.
   - Show source trace: what endpoint returned which resource.

2. **Bulk add raw nodes**
   - Paste IPs, hostnames, CIDRs, CSV, or simple JSON.
   - Classify as unknown, host, appliance, API, storage, repository, or placeholder.
   - Probe lightly when safe: ping, SSH banner, HTTP title, SNMP later, vendor API later.
   - Do not require everything to be a server.

3. **Introduce credentials**
   - Attach credentials to resources by scope, not by copying secrets into every node.
   - Example scopes: `ssh:lab-root`, `api:proxmox`, `snmp:network-read`, `git:deploy-read`.
   - Show which resources a credential can affect before using it.

4. **Normalize inventory**
   - Convert raw source facts into resource records.
   - Preserve source snapshots for audit and debugging.
   - Let one resource have many sources: Proxmox, SSH probe, manual note, DNS, Docker, Git.

5. **Create relationships**
   - Relate VM to Proxmox node, disk, network, IP, host record, pipeline, and service.
   - Relate Git repo to build action, deployment target, running service, and rollback tag.
   - Relate appliance interfaces to networks, VLANs, and services.
   - Treat relationships as first-class facts, not comments.

6. **Build actions**
   - Actions are reusable operation definitions with inputs, target kind, credential requirements, risk level, and expected outputs.
   - Examples: SSH update, render config, restart service, clone VM, sync inventory, stage Kickstart, tag Git release.
   - Actions can run directly or become pipeline stages.

7. **Compose capability blocks**
   - A capability block is a functional slice like Docker Swarm, monitoring, DNS/DHCP, web app, database, build worker, or backup storage.
   - Blocks contain resources plus relationships plus allowed actions.
   - This is close to Docker Compose/Kubernetes thinking: define the stack relationships and the operational verbs around them.

8. **Run and learn**
   - Executions produce jobs, logs, state changes, and new facts.
   - Results should update the graph: last seen, health, relationships, inventory source timestamps.
   - Failures should be attached to the resource/action that caused them.

## Detail Tabs

Top tabs should be consistent enough to learn, but flexible per resource type:

- **Summary**: identity, state, owner/source, last seen, primary risks, quick facts.
- **Relationships**: parent/child links, dependencies, affected groups, linked actions, upstream/downstream resources.
- **Actions**: runnable operations, dry-run notes, required permissions, target scope, recent result.
- **Inventory**: discovered facts, source snapshots, normalized metadata.
- **Config**: desired state, rendered templates, credentials boundary, environment settings.
- **Storage**: disks, capacity, artifacts, ISO/template media, repository size, object storage, cache state.
- **History**: BKC jobs, Proxmox tasks, SSH/admin history, API syncs, pipeline runs.
- **Logs**: selected job output, service logs, console snapshots, install notes.

Not every resource uses every tab. For example, a Git repository might use Summary, Relationships, Actions, Config, History, and Logs, while a VM additionally uses Storage and console-oriented Logs.

## Information Hierarchy

BKC should not reproduce the “README far below the fold” problem common in large GitHub projects. Operators should not have to scroll through a long page to answer basic questions.

Each resource view should start with a compact, purposeful summary:

- current state and health
- resource kind and source
- primary identifiers
- relationship highlights
- capacity or risk highlights when relevant
- next likely actions
- last run or last observed change

Long-form data should be available, but moved into the right place:

- raw inventory under **Inventory**
- dense config under **Config**
- logs and console output under **Logs**
- task history under **History**
- design notes or repository README content behind a preview/expand pattern

The Summary tab should be the operator's dashboard for the selected resource. It should answer “what is this, is it healthy, what is it related to, and what can I safely do next?” without requiring a page-down.

## Theme And Preferences

BKC should support light and dark themes as user/profile preferences:

- default to system preference for new users
- allow explicit light/dark override in profile settings
- store preference per user, not globally
- keep status colors accessible in both themes
- avoid theme-specific meaning; state should be conveyed by label/icon plus color
- make dense operational screens comfortable for long sessions

Theme selection belongs in profile/user settings, while platform-wide branding or default theme can remain an owner/superuser setting later.

## Relationship Model

Relationships should be typed so the UI can answer operational questions:

- `runs_on`: service runs on host or container.
- `hosted_by`: VM hosted by Proxmox node.
- `stored_on`: VM disk, backup, or repo cache stored on storage resource.
- `connected_to`: interface connected to network, VLAN, bridge, or switch port.
- `managed_by`: resource managed through API, SSH, Ansible, or pipeline.
- `uses_credential`: action or resource uses a credential scope.
- `built_from`: deployment built from Git repo/tag/artifact.
- `depends_on`: capability block depends on another block or service.
- `targets`: action targets a resource, group, query, or relationship set.
- `produces`: action produces artifact, config, VM, service, or inventory facts.

The Relationships tab should show both directions: “this resource depends on” and “resources depending on this.”

### Relationship Constraints

Not every resource kind should relate directly to every other kind. BKC should validate relationship types by source kind, target kind, and direction. This keeps the graph useful instead of becoming a bag of arbitrary links.

Examples of direct relationships that make sense:

| Relationship | Source Kind | Target Kind |
|--------------|-------------|-------------|
| `hosted_by` | VM, container | Proxmox node, host, cluster |
| `stored_on` | VM disk, backup, artifact | storage |
| `connected_to` | interface, VM, host, appliance | network, VLAN, switch port |
| `managed_by` | VM, host, appliance, service | API, pipeline, SSH credential scope |
| `uses_credential` | API, action, pipeline | credential |
| `built_from` | service, artifact, deployment | Git repository, release tag |
| `runs_on` | service, container, agent | VM, host, cluster |
| `targets` | action, pipeline stage | resource, group, query, capability block |
| `produces` | action, pipeline stage | artifact, config, resource, inventory fact |

Examples of relationships that should usually be indirect:

- A VM does not normally relate directly to a Git repository.
- A switch does not normally relate directly to a package update action.
- A credential does not normally relate directly to a storage volume unless the credential is specifically scoped to that storage API.

The relationship can still exist through an action or capability block. For example:

```text
Git repository
  built_by -> action: checkout repo
  produces -> working tree at /srv/app

action: checkout repo
  targets -> VM or host
  uses_credential -> git deploy key
  requires -> package/action: install git

service
  built_from -> Git repository
  runs_on -> VM or host
```

This preserves the important operational chain without pretending that the VM itself has a first-class direct dependency on the repository.

### Action-Mediated Relationships

Some useful relationships only become true after a sequence of actions. BKC should model the sequence as actions and produced facts:

```text
1. install package: git
   targets -> host
   produces -> capability: git-client

2. install credential: git deploy key
   targets -> host
   uses_credential -> credential:git-deploy
   produces -> file:/home/deploy/.ssh/id_ed25519

3. checkout repository
   targets -> host
   requires -> capability: git-client
   uses_credential -> credential:git-deploy
   built_from -> Git repository
   produces -> path:/srv/app

4. run build or deploy command
   targets -> path:/srv/app
   produces -> service, artifact, or health result

5. measure result
   targets -> service or endpoint
   produces -> health fact, log, metric, or failure
```

The graph should show both the durable relationships and the action trail that established them. Durable facts survive as inventory; action trails stay in history and can be promoted into a reusable pipeline when stable.

## Action Model

Actions should be small, typed, and composable:

```json
{
  "id": "action:ssh-update-fedora",
  "kind": "ssh-command",
  "name": "Update Fedora packages",
  "target_kinds": ["host", "vm", "appliance"],
  "credential_scope": "ssh:admin",
  "risk": "medium",
  "inputs": {
    "package_manager": "dnf"
  },
  "command": "dnf -y upgrade",
  "produces": ["job-log", "package-state"]
}
```

Actions can be presented as buttons, menu items, or pipeline stages depending on context. The same action should not need to be rewritten for every host if the relationship/target query can identify the right resources.

## Pipeline Designer

BKC should let operators both run and design pipelines, but the first designer should stay simple. It should not try to become a full visual programming environment or a complex JSON form builder.

The practical first version:

- pipeline metadata form: name, description, owner, repository/path, enabled flag
- ordered stage list with add/remove/reorder
- stage type selector: API call, SSH command, template render, Proxmox action, inventory sync, probe, manual checkpoint
- plain text editors for command/script/template fragments
- repository path fields for definitions stored and edited outside BKC
- simple variable fields for obvious inputs such as VMID, node, image name, target group, network, IP mode
- relationship preview: resources targeted, produced, or required by each stage
- run history and last output beside the design, not hidden elsewhere

The UI should bias toward readable stage definitions:

```text
Pipeline: fedora-template-install
  Source: repo://BlackKnightController/pipelines/fedora-template-install.yml
  Stages:
    1. select image or base VM
    2. ensure install media exists in Proxmox
    3. prepare DHCP/cloud-init/static IP strategy
    4. stage Kickstart or cloud-init data
    5. boot installer once
    6. set disk-first boot
    7. discover assigned IP
    8. wait for SSH
    9. run follow-up actions
    10. record inventory facts and relationships
```

### Install-Orchestration Pipeline

The recent Fedora/Proxmox work is a good archetype:

```text
Input resources:
  API: Proxmox
  VM/base image: target VM or template
  ISO/artifact: Fedora netinst or cloud image
  Network: lab bridge, isolated bridge, or inner install network
  Host/service: ns1 DHCP/HTTP/Kickstart server
  Credential: Proxmox API token, SSH key, optional Git deploy key

Stages:
  1. identify target VM or base image
  2. pull/import image into Proxmox if missing
  3. choose IP strategy:
       - DHCP discovery
       - DHCP reservation on ns1
       - Proxmox cloud-init static IP
       - isolated inner network served by lab DHCP
  4. stage install data:
       - Kickstart over HTTP
       - OEMDRV ISO
       - cloud-init seed
  5. adjust VM boot/media exactly once
  6. boot install
  7. set next boot to disk-first before reboot
  8. watch console/task/network signals
  9. discover IP and wait for SSH/API readiness
  10. run follow-up actions:
       - install packages
       - install key
       - pull repository to path
       - render config
       - start service
       - run health check
  11. write durable relationships:
       - VM hosted_by Proxmox node
       - VM connected_to network
       - VM stored_on storage
       - service built_from repository
       - service runs_on VM
       - pipeline produced inventory facts
```

This pipeline does not mean every resource is directly related. The pipeline creates the operational chain and records only the durable facts that remain true afterward.

### Network-Orchestrated Installs

BKC should support isolated install networks as a first-class pattern:

- create or select Proxmox bridge/VLAN
- attach target VMs to that network
- use `ns1` or another install controller to provide DHCP, DNS, TFTP/HTTP, and Kickstart/cloud-init
- discover leases and hostnames from DHCP state
- move or add network interfaces after install if required
- keep the install network relationship separate from the production network relationship

This matters for appliances and raw nodes too. A router, switch, NAS, or SAN may need a staging network, temporary credential, vendor API call, or config backup action before it becomes part of a capability block.

### Designer Guardrails

- Store advanced pipeline definitions in repository files when possible.
- Let the UI edit simple text fields and stage order; let external editors handle complex scripts.
- Show a stage's target resources and required credentials before run.
- Show produced facts/relationships after run.
- Support manual checkpoint stages for risky operations.
- Prefer small reusable actions over one huge script stage.
- Keep raw output visible for debugging, but promote successful outcomes into inventory facts.

## Capability Blocks

Capability blocks are how BKC should represent “whole stack” behavior without forcing everything into a single machine list.

Example monitoring block:

```text
Capability: monitoring
  Resources:
    Git repo: monitoring-stack
    Docker stack: monitoring
    Services: grafana, loki, prometheus
    Storage: metrics volume
    API: Docker manager
  Relationships:
    built_from monitoring-stack
    deployed_to swarm managers
    stores_on metrics volume
    exposes grafana endpoint
  Actions:
    deploy
    health check
    tail logs
    rollback
```

Example VM install block:

```text
Capability: fedora-template-install
  Resources:
    VM: fedora-template-115
    ISO: Fedora netinst
    ISO: OEMDRV kickstart
    API: Proxmox
    Host: ns1 HTTP kickstart server
  Relationships:
    VM hosted_by pve
    VM stored_on local-lvm
    Kickstart served_by ns1
    Action targets VM
  Actions:
    stage KS
    attach OEMDRV
    boot installer once
    set disk-first boot
    validate SSH
```

## Example Resource Views

### VM

- Summary: VMID, Proxmox node, status, IPs, OS, boot order.
- Relationships: BKC group, linked host record, storage volumes, pipelines, services.
- Actions: start, stop, reboot, SSH probe, run command, launch pipeline.
- Storage: disks, ISO attachments, capacity, template source.
- Logs: console snapshot, install notes, recent job output.

### Git Repository

- Summary: remote, branch, last commit, dirty state if local, deployment target.
- Relationships: pipelines that consume it, services deployed from it, hosts with checked-out copies.
- Actions: fetch, status, tag, build, sync to `ns1`, trigger pipeline.
- Config: deploy path, allowed branch, build command, credential policy.
- History: recent commits known to BKC, pipeline runs, sync jobs.

### API Interface

- Summary: endpoint, auth mode, readiness, last successful call.
- Relationships: resources discovered through it, pipelines depending on it, stored integration config.
- Actions: test connection, pull inventory, sync inventory, rotate token note.
- Config: base URL, TLS verification, rate limits, scopes.
- Logs: request trace and failures.

### Action

- Summary: action type, command/template/pipeline, risk level, expected duration.
- Relationships: target resource types, groups, required integration, previous runs.
- Actions: run, dry-run when supported, clone/edit action, disable.
- History: last results bucketed by target and exit status.

### Appliance

- Summary: vendor/model when known, management IP, role, firmware, reachability.
- Relationships: connected networks, upstream API/controller, credential scope, dependent services.
- Actions: backup config, pull inventory, check interfaces, test SNMP/API, open management URL.
- Inventory: interfaces, VLANs, routes, volumes, shares, serial/model facts.

### Capability Block

- Summary: purpose, health, owner, primary endpoints, version/source.
- Relationships: resources in the block, dependencies, consumers, credentials.
- Actions: deploy, validate, restart, scale, backup, rollback.
- History: pipeline runs, action results, config changes.

## Interaction Rules

- Selecting a tree node should never lose operator context; detail tabs update in place.
- Actions must show target scope before execution.
- The UI should favor dense facts over marketing-style cards.
- Relationship views should answer “what changes if I act here?”
- Complex resources can use top tabs plus a secondary split view inside the tab.
- Avoid VM-only language in shared components; use `resource`, `target`, `source`, `relationship`, and `action`.
- Icons should be consistent by `kind` and should never replace labels.
- Bulk import should preserve unknown resources instead of forcing early classification.
- Credential prompts should be explicit about scope and blast radius.
- Summary views should keep key facts above the fold.
- Deep text should use previews, tabs, or expandable sections instead of pushing core state off screen.

## Data Model Direction

The UI can evolve toward a normalized resource record:

```json
{
  "id": "proxmox:qemu:115",
  "kind": "vm",
  "name": "fedora-template-115",
  "state": "running",
  "summary": {},
  "relationships": [],
  "actions": [],
  "sources": ["proxmox", "ssh-probe", "manual-note"]
}
```

Existing inventory files can feed this view without an immediate migration. The first implementation can be an adapter over current Proxmox, Docker, Ansible, pipeline, and rules snapshots.

# Black Knight Controller

Black Knight Controller (BKC) is a lightweight control plane for lab and small
infrastructure operators. It brings inventory, integrations, SSH actions,
Ansible work, Docker Swarm state, Kubernetes checks, Proxmox lifecycle work, and
tracked automation pipelines into one operator surface.

BKC is not trying to hide infrastructure behind a wizard. It is built for people
who already use SSH, Ansible, Docker, `kubectl`, and hypervisor APIs, and want a
cleaner place to see what exists, run the next safe action, and turn repeated
repairs into reusable workflows.

![BKC overview and pipeline run example](docs/images/Screenshot1.png)

## What It Helps With

- See hosts, groups, VMs, APIs, containers, services, credentials, templates,
  and pipelines as related resources instead of separate notes and terminals.
- Connect to common lab execution paths: direct SSH, Ansible, Docker Swarm,
  Kubernetes, Proxmox, and HTTP APIs.
- Queue longer operations through Redis/RQ workers so the browser stays
  responsive.
- Track pipeline runs with stage state, event history, validation output, and
  failure context.
- Keep tenant-scoped runtime inventory, credentials, and snapshots out of the
  tracked source tree.

## A Typical BKC Story

A lab service is failing after a cluster change. The fix touches a VM setting,
host packages, container state, and log validation.

With BKC, the operator can:

1. Open the affected host, VM, service, or pipeline from the resource graph.
2. Check current state through the right integration instead of hunting through
   old shell history.
3. Run a small SSH command, Ansible playbook, Docker scan, Kubernetes query, or
   Proxmox action from a tracked UI path.
4. Move the working fix into a pipeline stage once it is repeatable.
5. Review the next run with stage history and validation output attached.

That keeps the first repair interactive while making the useful result
repeatable.

## Visual Tour

![BKC overview page](docs/images/Overview-page.png)

The overview page is the landing console: resource counts, inferred
relationships, active and failed run summaries, and jump points into the graph,
inventory, integrations, and pipelines.

![BKC resource graph](docs/images/ResourceGraph.png)

The resource graph shows APIs, clusters, groups, hosts, VMs, containers,
repositories, pipelines, actions, and credentials as related resources.

![BKC inventory console](docs/images/InventoryConsole.png)

The inventory console keeps resources first and launch paths underneath. Groups,
host facts, relationships, status grids, and direct inspect/deploy actions come
together here.

![BKC integrations screen](docs/images/API-Integrations.png)

The integrations screen is where operators store credentials, test endpoints,
pull inventories, and sync discovered resources into BKC.

![BKC pipelines screen](docs/images/PipeLines.png)

The pipelines screen turns manual fixes into tracked runs. Operators can browse
templates, inspect stages, queue work, and review recent run state.

## Core Concepts

### Resource Graph

BKC treats infrastructure as related resources: hosts, groups, VMs, Docker
services, Kubernetes contexts, API endpoints, repositories, templates,
credentials, actions, and pipelines. The goal is to answer:

- What exists?
- How is it related?
- What action is safe to run from here?
- What changed during the last run?

See [docs/ui-resource-graph.md](docs/ui-resource-graph.md) for the working
information architecture.

### Integrations

BKC can work with:

- SSH targets from inventory metadata
- Ansible controllers and playbooks
- Docker Swarm managers
- Kubernetes contexts through `kubectl -o json`
- Proxmox through the HTTPS API
- Local templates and tenant-scoped dictionaries

The right path depends on the task. SSH is useful for bootstrap and repair,
Ansible is useful for reusable host configuration, Docker and Kubernetes APIs
are better for platform state, and Proxmox owns VM lifecycle work.

### Pipelines

Pipelines are tracked automation runs made of reusable stages. A stage can
select a target, run an action, render a template, call an integration, collect
facts, or validate the result.

Older code paths may still use the word `workflow`, but the user-facing model is
now `pipeline`.

## Real Use Patterns

These are sanitized examples based on real BKC operating patterns. Replace host
groups, paths, service names, and endpoints with your own lab values.

### Deploy BKC To A Swarm With Persistent Runtime State

BKC can be built from a source snapshot and deployed as a small Swarm stack:

```yaml
- name: Render BKC stack file
  copy:
    dest: /opt/bkc/stack.yml
    mode: "0644"
    content: |
      version: "3.8"
      services:
        redis:
          image: redis:7-alpine
          command: ["redis-server", "--appendonly", "yes"]
          volumes:
            - /srv/bkc/runtime/redis:/data

        bkc:
          image: local/blackknightcontroller:latest
          environment:
            BKC_ACCESS_LOG_FORMAT: json
            BKC_RATELIMIT_STORAGE_URI: redis://redis:6379/0
            BKC_JOB_QUEUE_URL: redis://redis:6379/2
          ports:
            - "5000:5000"
          volumes:
            - /srv/bkc/runtime/dictionaries:/app/dictionaries
            - /srv/bkc/runtime/keys:/app/keys
            - /srv/bkc/runtime/file_templates:/app/file_templates

        bkc-worker:
          image: local/blackknightcontroller:latest
          command: ["python", "bkc_worker.py"]
          environment:
            BKC_JOB_QUEUE_URL: redis://redis:6379/2
          volumes:
            - /srv/bkc/runtime/dictionaries:/app/dictionaries
            - /srv/bkc/runtime/keys:/app/keys
            - /srv/bkc/runtime/file_templates:/app/file_templates
```

The important pattern is simple: source stays in Git, runtime state lives on a
persistent volume, and the worker shares the same runtime mounts as the web app.

### Seed A BKC Automation Key

A small access playbook can generate an SSH key for BKC and install the public
key on selected lab hosts:

```yaml
- name: Ensure BKC runtime key exists
  command:
    argv:
      - ssh-keygen
      - -q
      - -t
      - rsa
      - -b
      - "4096"
      - -N
      - ""
      - -f
      - /srv/bkc/runtime/keys/bkc_id_rsa
  args:
    creates: /srv/bkc/runtime/keys/bkc_id_rsa

- name: Install BKC public key for automation user
  lineinfile:
    path: /home/automation/.ssh/authorized_keys
    line: "{{ lookup('file', '/srv/bkc/runtime/keys/bkc_id_rsa.pub') | trim }}"
    create: true
    owner: automation
    group: automation
    mode: "0600"
    state: present
```

This gives Admin Mode and pipeline stages a predictable key without committing
private key material to the repo.

### Add Host Log Shipping And Validate It

Repeated troubleshooting often becomes a small Ansible lane. This pattern
installs a service, renders config, starts it, and checks readiness:

```yaml
- name: Install log shipper dependencies
  package:
    name:
      - unzip
      - curl
    state: present

- name: Render service config
  template:
    src: templates/log-agent.yml.j2
    dest: /etc/log-agent/config.yml
    owner: root
    group: root
    mode: "0644"
  notify: Restart log agent

- name: Ensure log agent is running
  service:
    name: log-agent
    state: started
    enabled: true

- name: Wait for readiness endpoint
  uri:
    url: http://127.0.0.1:9080/ready
    method: GET
    status_code: 200
  register: ready_probe
  retries: 10
  delay: 3
  until: ready_probe.status == 200
```

BKC is useful here because the readiness check can become pipeline evidence
instead of a one-time terminal observation.

### Run Fast SSH Checks From Admin Mode

BKC SSH mode is the lightweight execution path for readable, targeted commands.
Good examples are status checks, small repairs, and bootstrap work.

Check system health:

```bash
hostnamectl
systemctl --failed --no-pager
df -h
free -h
```

Check Docker Swarm state:

```bash
docker node ls
docker stack ls
docker service ls
```

Restart a service and verify it:

```bash
systemctl restart example-service
systemctl --no-pager --full status example-service
```

Patch a setting safely:

```bash
cp -a /etc/example/service.conf /etc/example/service.conf.bkc.bak
grep -q '^managed_by=' /etc/example/service.conf \
  && sed -i 's/^managed_by=.*/managed_by=bkc/' /etc/example/service.conf \
  || printf '\nmanaged_by=bkc\n' >> /etc/example/service.conf
systemctl reload example-service
```

Move repeated multi-step SSH commands into templates or pipeline stages once
they stabilize.

## Quick Start

Install dependencies for local development:

```bash
pip install -r requirements.txt
```

Start the containerized app and Redis:

```bash
docker compose up --build
```

Then open:

```text
http://localhost:5000
```

For first sign-in, set a one-time bootstrap password before startup:

```bash
export BKC_BOOTSTRAP_ADMIN_USERNAME=admin
export BKC_BOOTSTRAP_ADMIN_PASSWORD='replace-me'
```

Remove the bootstrap password from the runtime environment after the first
account exists.

## Runtime And Security Notes

BKC CE targets trusted homelab and small MSP-style lab use. It includes sign-in,
roles, tenant-scoped inventory files, encrypted stored secrets, API keys, audit
logging, readiness checks, and optional JSON access logs.

Runtime state is intentionally separate from source:

- `dictionaries/bkc.db` stores auth, RBAC, audit, and API key hashes.
- `dictionaries/tenants/<slug>/` stores tenant inventory and integration
  snapshots.
- `keys/` stores local generated secrets and automation keys.
- `file_templates/` stores shared templates, with optional tenant overrides.

Git ignores real runtime state so lab credentials and inventory do not end up in
the public repo.

For complete security details, see [SECURITY.md](SECURITY.md).

## API And Background Jobs

BKC exposes a small API under `/api/v1`:

- `GET /api/v1/health`
- `GET /api/v1/ready`
- `GET /api/v1/me`
- `GET /api/v1/inventory`
- `GET /api/v1/automation/runs`
- `GET /api/v1/automation/runs/<run_id>`
- `POST /api/v1/automation/trigger`

When `BKC_JOB_QUEUE_URL` points at Redis, selected integration actions and
subnet scans are queued for `python bkc_worker.py`. In Docker Compose, the app
uses Redis database `/0` for rate limits and `/2` for jobs.

## Deeper Docs

- [CONTRIBUTING.md](CONTRIBUTING.md) - branch workflow, validation, and security
  expectations for contributions.
- [SECURITY.md](SECURITY.md) - credential handling, encryption, reporting, and
  deployment guidance.
- [docs/pipeline-action-model.md](docs/pipeline-action-model.md) - target
  action and pipeline model.
- [docs/pipeline-folder-layout.md](docs/pipeline-folder-layout.md) - pipeline
  catalog structure.
- [docs/ui-resource-graph.md](docs/ui-resource-graph.md) - resource graph
  information architecture.
- [docs/docker-kubernetes-api-targets.md](docs/docker-kubernetes-api-targets.md)
  - Docker and Kubernetes API target notes.
- [docs/k3s-deployment-linkage.md](docs/k3s-deployment-linkage.md) - k3s
  deployment linkage notes.

## Development Checks

Install development dependencies:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

Run the normal checks from the repo root:

```bash
ruff check .
pytest
```

Tests use a temporary SQLite file, so local `dictionaries/bkc.db` data is not
touched.

## Contributing

Contributions, forks, experiments, and downstream lab-specific adaptations are
welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull
request, especially the security and secrets requirements.

## License

Black Knight Controller code is licensed under **GPL-3.0-or-later**.
Documentation, screenshots, diagrams, and other narrative or visual material are
licensed under **CC BY-SA 4.0** unless a file says otherwise. See [LICENSE](LICENSE)
and `LICENSES/` for details.

## Support

- Issues: <https://github.com/auzieman/BlackKnightController-main/issues>
- Security: see [SECURITY.md](SECURITY.md)

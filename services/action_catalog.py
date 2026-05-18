from __future__ import annotations

from copy import deepcopy

BUILTIN_ACTIONS = [
    {
        "id": "ssh.probe",
        "label": "Probe host",
        "kind": "ssh",
        "summary": "Collect basic host identity and reachability facts over SSH.",
        "target_kinds": ["host", "vm", "kubernetes-node"],
        "credential_scope": "ssh:admin",
        "risk": "low",
        "inputs": {},
        "validations": ["hostname returned", "command exits zero"],
        "produces": ["fact:ssh_reachable", "fact:hostname", "fact:uptime"],
        "status": "planned",
    },
    {
        "id": "ssh.install_bkc_key",
        "label": "Install BKC SSH key",
        "kind": "ssh",
        "summary": "Install the configured BKC public key into a target account.",
        "target_kinds": ["host", "vm", "kubernetes-node"],
        "credential_scope": "ssh:bootstrap",
        "risk": "medium",
        "inputs": {"user": "string", "public_key": "credential-ref"},
        "validations": ["authorized_keys contains public key", "passwordless SSH succeeds"],
        "produces": ["relationship:uses_credential"],
        "status": "planned",
    },
    {
        "id": "ssh.nfs.ensure_mounts",
        "label": "Ensure NFS mounts",
        "kind": "ssh",
        "summary": "Create mount points, maintain fstab entries, mount shares, and verify exact NFS mountpoints.",
        "target_kinds": ["host", "vm", "kubernetes-node"],
        "credential_scope": "ssh:admin",
        "risk": "medium",
        "inputs": {"mounts": "list", "install_package": "bool"},
        "validations": ["findmnt exact mountpoint", "fstype is nfs or nfs4"],
        "produces": ["fact:nfs_mounts", "relationship:uses_storage"],
        "status": "implemented",
    },
    {
        "id": "ssh.firewall.open_ports",
        "label": "Open firewall ports",
        "kind": "ssh",
        "summary": "Open a controlled list of host firewall ports and validate the service endpoint when possible.",
        "target_kinds": ["host", "vm", "kubernetes-node"],
        "credential_scope": "ssh:admin",
        "risk": "medium",
        "inputs": {"ports": "list", "probes": "list"},
        "validations": ["firewall reload succeeds", "configured probes answer"],
        "produces": ["fact:open_ports"],
        "status": "implemented",
    },
    {
        "id": "k3s.nodes.ready",
        "label": "Verify k3s nodes",
        "kind": "k3s",
        "summary": "Read cluster nodes through kube1 and wait for all nodes to report Ready.",
        "target_kinds": ["cluster", "kubernetes-node"],
        "credential_scope": "ssh:admin",
        "risk": "low",
        "inputs": {"timeout_seconds": "integer"},
        "validations": ["kubectl wait condition=Ready succeeds"],
        "produces": ["fact:k3s_nodes_ready"],
        "status": "implemented",
    },
    {
        "id": "k3s.manifest.apply",
        "label": "Apply k3s manifest",
        "kind": "k3s",
        "summary": "Render or copy a Kubernetes manifest through kube1, apply it, and optionally wait for rollout.",
        "target_kinds": ["cluster"],
        "credential_scope": "ssh:admin",
        "risk": "medium",
        "inputs": {"template": "string", "namespace": "string", "rollout": "string"},
        "validations": ["kubectl apply succeeds", "rollout status succeeds"],
        "produces": ["fact:k3s_manifest_applied"],
        "status": "implemented",
    },
    {
        "id": "prometheus.scrape_job.ensure",
        "label": "Ensure Prometheus scrape job",
        "kind": "prometheus",
        "summary": "Patch the shared Prometheus configuration with an idempotent scrape job block.",
        "target_kinds": ["api", "cluster", "kubernetes-node"],
        "credential_scope": "ssh:admin",
        "risk": "medium",
        "inputs": {"job_name": "string", "targets": "list"},
        "validations": ["Prometheus reload or service update succeeds"],
        "produces": ["relationship:scraped_by"],
        "status": "implemented",
    },
    {
        "id": "prometheus.targets.verify",
        "label": "Verify Prometheus targets",
        "kind": "prometheus",
        "summary": "Check Prometheus target health for expected jobs and instances.",
        "target_kinds": ["api", "cluster", "kubernetes-node"],
        "credential_scope": "read:observability",
        "risk": "low",
        "inputs": {"jobs": "list", "expected_up": "integer"},
        "validations": ["expected target count is up"],
        "produces": ["fact:prometheus_targets_up"],
        "status": "implemented",
    },
    {
        "id": "loki.stream.verify",
        "label": "Verify Loki stream",
        "kind": "loki",
        "summary": "Check that Loki has recent streams for a label selector.",
        "target_kinds": ["api", "cluster", "service"],
        "credential_scope": "read:observability",
        "risk": "low",
        "inputs": {"query": "string", "lookback_seconds": "integer"},
        "validations": ["query returns at least one stream"],
        "produces": ["fact:loki_stream_present"],
        "status": "planned",
    },
    {
        "id": "docker.service.force_update",
        "label": "Force update Docker service",
        "kind": "docker",
        "summary": "Trigger a Docker Swarm service refresh from a configured manager.",
        "target_kinds": ["service", "host", "cluster"],
        "credential_scope": "ssh:admin",
        "risk": "medium",
        "inputs": {"service": "string"},
        "validations": ["service update accepted", "replicas converge"],
        "produces": ["fact:service_refreshed"],
        "status": "planned",
    },
    {
        "id": "repo.git.status",
        "label": "Check repository status",
        "kind": "repo",
        "summary": "Inspect a repository working tree and current commit.",
        "target_kinds": ["repo", "host"],
        "credential_scope": "ssh:admin",
        "risk": "low",
        "inputs": {"path": "string"},
        "validations": ["git status exits zero"],
        "produces": ["fact:git_branch", "fact:git_commit", "fact:git_dirty"],
        "status": "planned",
    },
]


def list_actions() -> list[dict]:
    return deepcopy(BUILTIN_ACTIONS)


def action_by_id(action_id: str) -> dict | None:
    normalized = str(action_id or "").strip()
    for action in BUILTIN_ACTIONS:
        if action["id"] == normalized:
            return deepcopy(action)
    return None


def actions_for_kind(resource_kind: str) -> list[dict]:
    normalized = str(resource_kind or "").strip().lower()
    return [
        deepcopy(action)
        for action in BUILTIN_ACTIONS
        if normalized in {str(kind).strip().lower() for kind in action.get("target_kinds", [])}
    ]

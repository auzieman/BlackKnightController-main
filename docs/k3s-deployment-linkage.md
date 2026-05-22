# K3s Deployment Linkage

## Current Lab Path

BKC should keep using its SSH executor as the first-class path for the lab k3s cluster:

1. Build or stage artifacts from the BKC-controlled source path.
2. Use BKC SSH credentials to reach `kube1.lab.auzietek.com`.
3. Run `k3s kubectl apply` from a shared mount or from a manifest staged by BKC.
4. Verify rollout status and run HTTP smoke checks as pipeline stages.

This keeps deployments visible as normal BKC runs and avoids undocumented manual `kubectl` sessions on kube1.

## Near-Term Improvement

The current `rx-demo-k3s-app-refresh` lane still depends on `/mnt/swarm/shared/rx-demo` being visible on kube1. The more reliable SSH version is:

1. Render or package the manifest on the manager side.
2. Copy the rendered manifest to kube1 through BKC SSH.
3. Run `k3s kubectl apply -f /tmp/<pipeline>.yaml`.
4. Keep the same rollout and smoke-check stages.

That removes the kube-side source-tree dependency while staying inside the existing BKC SSH action model.

## Native Kubernetes Mode

A later `kubernetes` integration can store a kubeconfig or service-account token in BKC settings and run Kubernetes API operations directly from the BKC worker. That should be a separate executor transport, not a replacement for SSH:

- `bkc-ssh`: good for host bootstrap, k3s install, local image import, NFS, firewall, and emergency repair.
- `kubernetes-api`: good for namespace, apply, rollout, events, pod logs, service discovery, and health summaries.
- `ansible`: useful where an existing playbook already owns host state, but it should not be required for basic BKC deploy lanes.

The pipeline JSON should stay transport-neutral: stages describe intent and inputs, while the executor chooses `bkc-ssh`, `kubernetes-api`, or `ansible` based on the action template.

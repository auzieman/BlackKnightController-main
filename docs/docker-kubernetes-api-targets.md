# Docker And Kubernetes API Targets

BKC should treat Docker Swarm and k3s as direct API targets. Ansible remains useful
for host bootstrap and package drift, but build, deploy, scan, and health actions
should prefer Docker or Kubernetes APIs when the platform already exposes them.

## Workstation Defaults

The workstation has these named targets:

```sh
docker context use auzix-swarm
kubectl config use-context auzix-k3s
```

The Docker CLI can also bypass context state for a single command:

```sh
docker --host ssh://root@swarm1.lab.auzietek.com node ls
DOCKER_HOST=ssh://root@swarm1.lab.auzietek.com docker node ls
```

Use the single-command form in automation when the target should be explicit and
repeatable. Use Docker contexts for an operator shell.

## BKC Runtime Contract

The BKC container image includes:

- `docker`
- `kubectl`
- `openssh-client`

The Compose runtime mounts:

- `./keys` at `/app/keys`
- `./docker/bkc/ssh_config` at `/root/.ssh/config`
- `${HOME}/.kube/config` at `/app/runtime/kube/config`

Current integration settings should point to:

```json
{
  "docker": {
    "api_mode": "host",
    "api_endpoint": "ssh://root@swarm1.lab.auzietek.com",
    "context_name": "auzix-swarm"
  },
  "kubernetes": {
    "context": "auzix-k3s",
    "kubeconfig_path": "/app/runtime/kube/config",
    "namespace": "auzix-build"
  }
}
```

The BKC public key must be installed on the Docker manager for SSH transport.
The current lab has that key installed on `root@swarm1.lab.auzietek.com`.

## Division Of Labor

- Docker API: swarm nodes, stacks, services, image build/import, service update,
  stack deploy/remove, log reads.
- Kubernetes API: namespaces, apply/delete, rollout status, pod/service/event/log
  reads.
- SSH: OS bootstrap, emergency repair, one-off host inspection, and key
  installation.
- Ansible: broader host convergence when repeated package and config state matters.

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

## Lab Swarm Contexts Behind The Edge

The operator workstation usually does **not** have a direct route to
`10.20.0.0/24` or OpenStack tenant networks. Do not build Docker contexts that
assume those routes exist. Use SSH aliases that jump through the reachable lab
edge host.

Example `~/.ssh/config` entries:

```sshconfig
Host bkc-edge
  HostName swarm1.lab.auzietek.com
  User root

Host bkc-esxi-swarm-mgr-01
  HostName 10.20.0.121
  User admin-deploy
  ProxyJump bkc-edge

Host bkc-openstack-swarm-mgr-01
  HostName 172.24.10.182
  User admin-deploy
  ProxyJump bkc-edge,root@10.20.0.240
```

Then create workstation Docker contexts against those SSH aliases:

```sh
docker context create bkc-esxi-swarm \
  --docker "host=ssh://bkc-esxi-swarm-mgr-01"

docker context create bkc-openstack-swarm \
  --docker "host=ssh://bkc-openstack-swarm-mgr-01"
```

Validation:

```sh
docker --context bkc-esxi-swarm node ls
docker --context bkc-openstack-swarm node ls
```

For the OpenStack swarm, prefer a floating IP or an explicit routed management
network once the video path graduates from lab proof to daily-use operations.
Until then, treat the Server1 jump as the documented lab route.

## Portainer Edge Endpoints

Keep Portainer itself on the existing main swarm and attach new swarms through
Portainer Agent rather than exposing the Docker TCP API.

On each new swarm manager:

```sh
docker network create --driver overlay portainer_agent_network
docker service create \
  --name portainer_agent \
  --mode global \
  --network portainer_agent_network \
  --publish mode=host,target=9001,published=9001 \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --mount type=bind,src=/var/lib/docker/volumes,dst=/var/lib/docker/volumes \
  portainer/agent:latest
```

The lab edge exposes swarm agents as stable Portainer Agent endpoints:

```text
swarm1.lab.auzietek.com:19091 -> 10.20.0.121:9001
swarm1.lab.auzietek.com:19092 -> 10.20.0.230:9001
```

Add those endpoints in Portainer as Agent environments. Keep the public-facing
endpoint stable even when the backend manager changes; lab-edge nginx or IPFire
NAT can absorb that move.

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

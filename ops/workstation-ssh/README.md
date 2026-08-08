# Workstation SSH Forwarders for the Lab

The workstation should not need direct routes to every private lab address.
Use a small SSH profile instead:

```text
workstation
  -> ns1 / 192.168.1.10
      -> 10.20.0.240 server1 OpenStack
      -> 10.20.0.254 IPFire
      -> 10.20.0.114 ESXi
      -> 10.20.0.232 OpenStack floating/service lane
      -> 10.20.0.130 R730 AI worker
```

Install the profile:

```bash
ops/workstation-ssh/install-bkc-lab-ssh-profile.sh
```

Useful commands:

```bash
ssh lab-openstack hostname
ssh lab-ipfire hostname
ssh lab-esxi vmware -v
ssh lab-esxi-swarm-mgr docker info
ssh lab-openstack-swarm-mgr docker info
ssh lab-k3s-esx 'k3s kubectl get nodes -o wide'
ssh lab-ai-worker 'ollama list'

ssh -N lab-bkc-ipfire-tunnel
curl http://127.0.0.1:15000/login

ssh -N lab-bkc-openstack-tunnel
ssh -N lab-openstack-tunnels
ssh -N lab-ai-worker-tunnels
```

Create tidy Docker contexts:

```bash
ops/workstation-ssh/setup-lab-contexts.sh

docker --context bkc-edge-swarm ps
docker --context bkc-esxi-swarm node ls
docker --context bkc-openstack-swarm node ls
kubectl --context bkc-esxi-k3s get all -A
```

Current readiness notes:

- `bkc-edge-swarm` is ready when `ssh lab-edge docker info` works.
- `bkc-esxi-swarm` requires the SSH user to be allowed to read
  `/var/run/docker.sock` on the ESXi-hosted swarm manager.
- `bkc-openstack-swarm` requires the operator/BKC public key installed for
  `admin-deploy@10.20.0.232` or the alias adjusted to the approved managed user.

Local tunnel ports:

| Local port | Target | Purpose |
| --- | --- | --- |
| `15000` | `10.20.0.232:5000` | OpenStack-hosted BKC |
| `18080` | `10.20.0.232:18081` | OpenStack micro-blog canary |
| `18443` | `10.20.0.240:443` | OpenStack/Horizon direct |
| `19092` | `10.20.0.240:19092` | OpenStack Portainer Agent tunnel |
| `11143` | `10.20.0.130:11434` | R730 Ollama API |
| `18088` | `10.20.0.130:8080` | R730 OpenWebUI, once enabled |

## Guardrail

Prefer these named aliases in notes and pipelines:

```text
lab-ns1
lab-edge
lab-openstack
lab-ipfire
lab-esxi
lab-esxi-swarm-mgr
lab-openstack-swarm-mgr
lab-ai-worker
lab-bkc-ipfire-tunnel
```

Raw `10.20.0.x` targets are fine inside BKC or ns1, but workstation workflows
should go through a named SSH path so the laptop does not depend on fragile
temporary routes.

IPFire itself is managed as a BKC pipeline/API resource; do not treat local SSH
tunnels as the source of truth for IPFire state. `lab-bkc-ipfire-tunnel` is only
an operator convenience when Docker contexts or browser access need a stable
local path. Keep `lab-bkc-openstack-tunnel` as a fallback direct service-lane
tunnel while the lab routing story is still evolving. The operator context stays
stable even if the implementation changes from SSH forwarding to IPFire NAT,
VPN, or routed VLANs.

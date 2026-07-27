# Swarm And Edge Wrap For Publish

This is the wrap-up contract for the final two demo videos.

## Pipelines

- `40 VIDEO — OpenStack Docker Swarm Seed`
  - Creates the OpenStack-hosted Docker Swarm guests.
  - Bootstraps Docker Swarm inside OpenStack.
  - Uses OpenStack API plus BKC SSH.
  - Needs the OpenStack tenant SSH route checked before daily-use Docker context
    access.

- `50B CANDIDATE — ESXi Docker Swarm Seed`
  - Starts from known-good `bkc-trixie-base`.
  - Clones five ESXi guests.
  - Pins generated MACs in ns1 DHCP.
  - Installs Docker and validates a five-node Swarm.

## Edge Access

The workstation normally cannot route directly to `10.20.0.0/24`, so published
access must go through the lab edge:

- Web UIs go through `deploy/lab-edge`.
- Docker CLI contexts use SSH aliases with `ProxyJump`.
- Portainer uses Agent endpoints, not raw Docker TCP.

Known ESXi proof:

```text
ESXi swarm manager: 10.20.0.121
Docker context route: ssh://bkc-esxi-swarm-mgr-01 via ProxyJump bkc-edge
Portainer agent route: swarm1.lab.auzietek.com:19091 -> 10.20.0.121:9001
```

OpenStack proof is control-plane healthy, but tenant SSH routing still needs the
final management route/floating IP decision before it should be presented as a
workstation-native Docker context.

## Future OpenStack networking cleanup

For the polished lab model, OpenStack should expose operator-facing services on
an explicit provider/management network backed by the second physical NIC or the
10.20 lab bridge. Tenant/internal application traffic can remain on
`172.24.10.0/24`.

Target model:

```text
10.20.0.0/24   provider / management / operator-visible services
172.24.10.0/24 tenant / internal application network
```

The durable Portainer route for the OpenStack swarm is:

```text
Portainer -> tcp://swarm1.lab.auzietek.com:19092 -> lab-edge stream -> 10.20.0.230:9001
```

The earlier namespace proxy through `10.20.0.240:19092` is retired. The
OpenStack swarm manager now has a provider/service-side address on `10.20.0.230`,
so Portainer/BKC can reach the agent without tunneling through tenant-only
`172.24.10.0/24` addresses.

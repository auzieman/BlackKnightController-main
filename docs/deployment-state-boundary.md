# BKC Deployment State Boundary

BlackKnight Controller is split between versioned application source and
host-local runtime state. Deployments must preserve this boundary.

## Versioned source

- Repository: `BlackKnightController`
- Swarm build context: `/mnt/swarm/blackknightcontroller/src`
- Stack build file: `/srv/stacks/blackknightcontroller/build-compose.yml`
- Stack definition: `/srv/stacks/blackknightcontroller/docker-stack.yml`
- Image used by the lab stack: `blackknightcontroller:swarm-lab`

Source synchronization may update application code, templates, tests, and
sample configuration. It must exclude `.git`, virtual environments, caches,
screenshots, local databases, tenant data, keys, and runtime dictionaries.

## Host-local runtime state

The following paths remain outside the source checkout and are bind-mounted
into the BKC services:

- `/mnt/swarm/blackknightcontroller/runtime/keys`
- `/mnt/swarm/blackknightcontroller/runtime/dictionaries`
- `/mnt/swarm/blackknightcontroller/runtime/file_templates`

Redis also holds live queue and job state. Runtime keys, credentials,
integration dictionaries, generated tenant data, and queue state must not be
copied into Git or replaced by a source deployment.

The local developer checkout may contain a private `keys/` directory for
direct testing. That directory is not the authority for the Swarm deployment;
the mounted runtime key is. Target hosts must authorize the public key from
the Swarm runtime directory when BKC will manage them.

## AuziX VM130 workflow

The `auzix-vm130-deploy` workflow reads generated AuziX files from:

`/srv/nfs/swarm/AuziX/src/out/auzix-strict/AuzixRoot`

The source revision is recorded in:

`/srv/nfs/swarm/AuziX/src/.auzix-commit`

The workflow deploys the startup ownership repair and Midori wrapper to VM130
at `192.168.1.164`, then validates user-state writability, DNS, and HTTPS.
Each successful deployment writes a commit marker below
`/System/State/deployments` on the target.

## Deployment procedure

1. Synchronize the repository source into the Swarm build context using the
   exclusions above.
2. Build `blackknightcontroller:swarm-lab`.
3. Force-update `blackknight_bkc` and `blackknight_bkc-worker`.
4. Wait for both services to converge and verify the BKC health endpoint.
5. Run the appropriate BKC pipeline and retain its run record as the
   deployment audit trail.

Emergency direct repairs are acceptable when BKC is unavailable, but the same
logic must be backfilled into a pipeline and exercised afterward.

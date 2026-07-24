# OpenStack Lab Seed and Validate

Post-installer seed and validation lane for the BKC OpenStack lab.

This pipeline begins after the OpenStack installer provider has produced API
credentials, normally as a `clouds.yaml` entry. From that point forward, BKC
should manage the cloud through OpenStack APIs rather than host-local shell.

Initial seed target:

- Horizon dashboard URL
- Keystone API auth validation
- `bkc-demo` project
- `bkc-demo-admin` user
- provider network
- self-service tenant network and router
- tiny smoke flavor
- CirrOS smoke image
- BKC keypair
- SSH/ICMP security group
- one smoke instance

This gives the lab a crisp first proof:

```text
base hardware rebuilt
  -> OpenStack installed
  -> Horizon reachable
  -> BKC authenticates through OpenStack API
  -> BKC creates tenant primitives
  -> BKC launches a VM
  -> BKC records validation evidence
```

The same seed model is also the basis for a wipe/rebuild loop. After the cloud
can be seeded and a smoke VM can boot, the bare-metal reset lane should wipe the
host and repeat the full path from PXE through API validation.

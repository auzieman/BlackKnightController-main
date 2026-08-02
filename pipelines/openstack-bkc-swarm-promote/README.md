# OpenStack BKC Swarm Promote

Promote the canonical lab BlackKnightController stack that lives in the
OpenStack Docker Swarm.

This is the current primary lab UI path:

```text
bkc.lab.auzietek.com
  -> lab-edge nginx
  -> 10.20.0.232:5000
  -> OpenStack swarm stack bkc-alt
```

The older edge BKC on `192.168.1.15:5000` is now utility/fallback only. It may
remain useful for IPMI, wake-up, edge routing, and CLI helper work, but it is
not the canonical Company Mind UI target.

## Known-good guardrail

Do not update only the edge controller and assume `bkc.lab.auzietek.com` changed.
The named lab BKC route points at the OpenStack swarm.

## Current validation

The `resources-tray-explainer-20260802093237` image was built on `bkc-build-01`, pushed to
the lab registry, and promoted to the OpenStack BKC services after the
IONOS graph/search cleanup pass. It should be promoted to:

- `bkc-alt_bkc`
- `bkc-alt_worker`

through Portainer endpoint `6` named `OpenStack Docker Swarm`.

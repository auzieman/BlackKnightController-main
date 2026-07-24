# 40 CANDIDATE — OpenStack Local AI + OpenWebUI Preflight

This candidate converts the earlier Docker/service demo pattern into the
OpenStack lab without touching the proven `10`, `20`, `10B`, or `30` lanes.

The first pass is deliberately small:

1. prove Server1/OpenStack capacity and egress;
2. ensure a cattle Debian AI VM exists on `lab-internal`;
3. record the guarded Ollama/OpenWebUI fragments for the follow-up video.

## Why this shape

The small-office services lane is the best reusable pattern for this bonus
video: declare service intent, make one real change per stage, validate at the
same boundary, and leave a receipt. The rx-demo lane contributes the
build/deploy/validate rhythm, but k3s is intentionally not required here.

## Known-good boundaries

- `30 VIDEO` already proves OpenStack project, image, flavor, keypair, network,
  and smoke-server creation.
- This lane reuses those OpenStack mechanics and creates only
  `bkc-local-ai-01` plus `bkc.ai.small`/`bkc-local-ai-allow` if absent.
- Docker/Podman/Ollama/OpenWebUI installation is gated off by default until the
  VM landing zone passes twice.

## Promotion gate

Promote to `40 VIDEO` only after two candidate runs prove:

- OpenStack API token and service health;
- `bkc-local-ai-01` reaches `ACTIVE`;
- selected image/flavor/network/security group are present;
- no direct shell repair was needed.

# IONOS Lab DNS API Inventory

Status: candidate, read-only by default

This pipeline is the safe first lane for bringing the IONOS DNS API into BKC.
It targets `lab.auzietek.com` first so we can prove provider inventory, DNS
drift detection, and later DNS-01 certificate automation without touching the
main public site cutover.

## Guardrails

- The actual IONOS API key must never be committed.
- BKC should resolve it from `secret:ionos/dns/api`.
- Default mode is inventory and proposed changes only.
- DNS writes require `enable_dns_writes=true`.
- Main-site records under `auzietek.com` are explicitly out of scope here.

## Intended first pass

1. Resolve the DNS API secret at runtime.
2. List IONOS DNS zones.
3. Find the zone that owns `lab.auzietek.com`.
4. Snapshot matching lab records.
5. Compare those records to the desired lab edge map.
6. Produce a proposed change set.
7. Stop before writes unless explicitly enabled.

## Later SSL lane

Once the DNS inventory/write path is proven, reuse the same secret handling for
IONOS SSL/DNS-01 work. Keep SSL as a separate stage or pipeline so certificate
automation cannot accidentally become part of a casual DNS inventory run.

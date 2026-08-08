# Codex / BKC operating notes

Short-memory guardrails for lab work:

- Use `bkc-cli` for pipeline and run visibility before raw shell debugging.
- If a task is repeatable, make or update a BKC pipeline instead of leaving a one-off command trail.
- Treat `bkc.lab.auzietek.com` / OpenStack BKC as home; edge BKC is fallback.
- DNS secrets live on `ns1` root; lab DNS records belong in the `auzietek.com` IONOS zone under `*.lab.auzietek.com`.
- Public lab hostnames generally CNAME to `swarm1.lab.auzietek.com`; the actual service exposure is an edge route in `deploy/lab-edge`.
- IPFire tunnels, NATS/firewall rules, and route intent should be BKC inventory and pipeline-driven.
- R730 / `lab-ai-worker` is the AUZiX package/build worker; prefer BKC-triggered runs there over local laptop builds.

Pipeline debt from the ticket-host bringup:

- Add a `lab-edge-host-route-publish` pipeline that updates the lab-edge nginx config, bumps the immutable swarm config name, deploys the stack, and validates host-header HTTP/TLS routing.
- Add an `ipfire-lab-route-publish` pipeline that declares tunnels/firewall/NATS route intent, applies gated changes, and validates from BKC, ns1, edge, and selected lab guests.

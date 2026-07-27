# lab.auzietek.com DNS + Let's Encrypt Prepare

Candidate pipeline for turning `lab.auzietek.com` into the safe named lab edge.

This is intentionally split into gated steps:

1. install helpers on ns1
2. settle the bounded lab name map
3. backfill stable host records from inventory
4. snapshot current IONOS DNS state
5. optionally apply the lab DNS map
6. optionally request the Let's Encrypt wildcard cert
7. optionally spool certs to service targets
8. validate DNS/TLS

The IONOS key is always a secret reference and must not be committed.

Near-term “lab” means current stable roles plus near pipeline targets, not every
temporary VM. New fleet nodes should use platform-qualified names such as
`kube1-esx.lab.auzietek.com`, `swarm1-esx.lab.auzietek.com`, and
`swarm1-os.lab.auzietek.com`. Friendly unqualified names such as
`grafana.lab.auzietek.com` are service entry points, while platform-scoped
service names such as `grafana-esx.lab.auzietek.com` can exist when both lanes
need visible service edges.

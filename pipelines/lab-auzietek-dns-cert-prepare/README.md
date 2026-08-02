# lab.auzietek.com DNS + Let's Encrypt Prepare

Candidate pipeline for turning `lab.auzietek.com` into the safe named lab edge.

Near-term hostname contract:

```text
hostname.lab.auzietek.com
```

Every stable lab host or platform node should have one canonical record in
`ops/lab-certs/lab-auzietek-map.json`. Friendly service names can then point at
the canonical host with CNAMEs. This gives BKC a two-pronged validation feature:

1. applying the DNS map proves the IONOS API path works;
2. resolving the names proves inventory, edge links, and certificates can use
   stable hostnames instead of drifting IP notes.

This is intentionally split into gated steps:

1. install helpers on ns1
2. settle the bounded lab name map
3. backfill stable host records from inventory
4. snapshot current IONOS DNS state
5. optionally apply the lab DNS map
6. validate the desired DNS map through the IONOS API
7. optionally request the Let's Encrypt wildcard cert
8. optionally spool certs to service targets
9. validate DNS/TLS

The IONOS key is always a secret reference and must not be committed.

Near-term “lab” means current stable roles plus near pipeline targets, not every
temporary VM. New fleet nodes should use platform-qualified names such as
`kube1-esx.lab.auzietek.com`, `swarm1-esx.lab.auzietek.com`, and
`swarm1-os.lab.auzietek.com`. Friendly unqualified names such as
`grafana.lab.auzietek.com` are service entry points, while platform-scoped
service names such as `grafana-esx.lab.auzietek.com` can exist when both lanes
need visible service edges.

## Safe operator path

Install helpers on ns1:

```bash
ops/lab-certs/deploy-to-ns1.sh root@192.168.1.10
```

Place the IONOS key on ns1 without committing it:

```bash
install -d -m 0700 /root/.secrets
install -m 0600 /dev/stdin /root/.secrets/ionos-api-key
```

Then apply and validate the bounded lab map:

```bash
/opt/bkc-lab-certs/apply-and-validate-lab-map.sh
```

The wrapper writes receipts to `/tmp/bkc-lab-dns-*.json` on ns1. Those receipts
are suitable BKC evidence artifacts, but must be reviewed before committing
because provider responses may contain record IDs.

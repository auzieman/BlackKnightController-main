# AUZIX PUBLIC 40 — Publish Beta Repo + ISO Shelf

Visible pipeline lane for publishing the AUZiX beta shelf:

```text
https://auzix.auzietek.com/
  index.html
  repo/
  isos/
  receipts/
```

The build authority stays in the lab. `lab-build`/R730 produces the package
repo, ISO candidates, checksums, and receipts. The Auzietek IONOS VPS is only
the public mirror and nginx front door.

## Default safety posture

The default pipeline run is intentionally non-destructive:

1. verify lab artifacts exist;
2. run public-safety checks;
3. render the static landing shelf in `landing_only` mode;
4. dry-run the rsync;
5. snapshot DNS;
6. smoke URLs if already live;
7. record a fragment.

The following gates must be deliberately enabled for external changes:

- `enable_public_rsync`
- `enable_nginx_apply`
- `enable_dns_apply`
- `enable_wildcard_cert_deploy`
- `enable_cert_request`
- `enable_secondary_sync`

`landing_only` defaults to `true` until the next AUZiX ISO/install test blesses
the repo, ISO, and receipt payloads. In that mode the shelf renderer creates the
landing page plus lightweight placeholder `repo/`, `isos/`, and `receipts/`
paths, but does not copy package archives or ISO images.

TLS should normally reuse the existing `*.auzietek.com` star certificate. The
certificate-request gate is fallback/renewal work, not the first path for
`auzix.auzietek.com`.

DNS changes should run through ns1's IONOS DNS helper. The API key lives on ns1
under `/root/.secrets/ionos-api-key`; pipeline receipts must reference the
secret path only and never copy or print the key.

The operational facts live in BKC inventory:

- `ops/remote-sites/auzietek-public-inventory.json`

The pipeline should load that inventory first and use IDs such as
`service:auzix-public-beta-shelf`, `dns:auzix.auzietek.com`,
`tool:ns1-ionos-dns-helper`, and `cert:wildcard-auzietek-com` instead of
hand-maintaining one-off host/cert/DNS facts.

## Public content contract

Public media must not contain lab-only credentials or shortcuts:

- no SSH authorized keys;
- no lab password hashes;
- no BKC/private API URLs;
- no `10.20.*` or `192.168.1.*` bootstrap assumptions in public docs;
- no build cache or raw secrets.

Package repos may publish package archives, indexes, checksums, and selected
receipts. They must not publish private workdirs or runtime secrets.

## Related lanes

- `auzix-package-repo-stripped-iso` builds and validates package repo / ISO
  candidates.
- `auzietek-vps-ops-inventory` snapshots the public VPS state.
- `auzietek-vps-backup-prepare` should run before a public apply.
- `ionos-lab-dns-api-inventory` and `lab-auzietek-dns-cert-prepare` are the
  existing DNS/API/helper patterns this lane follows.

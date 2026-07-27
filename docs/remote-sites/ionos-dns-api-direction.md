# IONOS DNS API direction

Status: candidate remote DNS automation lane; lab zone first

Date: 2026-07-26

## Purpose

Bring Auzietek DNS under BKC visibility and controlled automation without
placing provider API credentials in Git or shell history.

This supports:

```text
lab.auzietek.com proving ground
beta.auzietek.com site proving ground
future auzietek.com cutover
Let's Encrypt / DNS-01 validation
Google Workspace / Gmail mail routing later
remote-site records for IONOS and lab VPN
DNS drift detection
```

## Scope decision

Start with `lab.auzietek.com`.

That is the safe API proving ground because it lets BKC exercise provider DNS
inventory, record comparison, controlled writes, TTL handling, and later
DNS-01 certificate validation without touching the public `auzietek.com`
cutover path.

Promotion order:

```text
1. read-only inventory for lab.auzietek.com
2. gated test record under lab.auzietek.com
3. DNS-01 / SSL validation experiment under lab.auzietek.com
4. beta.auzietek.com cleanup once proven
5. auzietek.com main-site cutover only after content and rollback are ready
```

Authority model:

```text
lab.auzietek.com
  -> safe proving ground for DNS API writes and SSL experiments

beta.auzietek.com
  -> public proving ground for the next Auzietek site

kb.auzietek.com / Kanboard
  -> planning, checklist, issue and promotion workflow

auzietek.com
  -> production target; no casual writes
```

Main-site DNS and SSL changes require a named promotion step, an expected diff,
validation commands, and rollback notes.

## Credential handling

IONOS DNS API requests use an API key in the `X-API-Key` header.

The key is composed as:

```text
<public-prefix>.<secret>
```

In BKC this must be represented only as a secret reference:

```text
secret:ionos/dns/api
```

Do not commit:

```text
public prefix
secret
combined X-API-Key
DDNS update URLs
provider tokens
```

Runtime convention:

```text
IONOS_DNS_API_KEY_REF=secret:ionos/dns/api
```

For local one-off tests, prefer an environment variable supplied outside Git:

```text
IONOS_API_KEY='<public-prefix>.<secret>'
```

## API facts

Official IONOS docs:

```text
DNS API docs: https://developer.hosting.ionos.com/docs/dns
API key docs: https://developer.hosting.ionos.com/docs/getstarted
SSL API docs: https://developer.hosting.ionos.com/docs/ssl
```

Useful contract:

```text
authorization header: X-API-Key
DNS zones endpoint:    https://api.hosting.ionos.com/dns/v1/zones
```

The IONOS help docs also describe Dynamic DNS keys as a public prefix plus
private key separated by a dot.

IONOS provides Swagger/OpenAPI-style API documentation for the DNS and SSL
surfaces. BKC should treat the provider docs/schema as the API contract and keep
our helper thin:

```text
OpenAPI/Swagger docs
  -> generate or validate client behavior

BKC helper
  -> secret handling, desired-state diff, apply/rollback, evidence

pipeline
  -> gated inventory/apply/validate stages
```

Avoid burying provider semantics in one-off shell fragments.

## First safe BKC behavior

Start read-only:

```text
list zones
find lab.auzietek.com or parent auzietek.com zone
list records for lab.auzietek.com scope
snapshot DNS state
compare expected vs actual records
record drift as evidence
```

Expected non-destructive checks:

```sh
curl -fsS \
  -H "X-API-Key: ${IONOS_API_KEY}" \
  https://api.hosting.ionos.com/dns/v1/zones
```

Then records:

```sh
curl -fsS \
  -H "X-API-Key: ${IONOS_API_KEY}" \
  "https://api.hosting.ionos.com/dns/v1/zones/${ZONE_ID}"
```

## Managed record intent

Initial lab records:

```text
bkc.lab.auzietek.com          -> current lab BKC edge
edge.lab.auzietek.com         -> lab edge landing page
grafana.lab.auzietek.com      -> lab Grafana edge
portainer.lab.auzietek.com    -> lab Portainer edge
openstack.lab.auzietek.com    -> lab Horizon edge
proxmox.lab.auzietek.com      -> pve1 edge
esxi.lab.auzietek.com         -> server2 ESXi edge
ipfire.lab.auzietek.com       -> IPFire candidate edge
switch.lab.auzietek.com       -> managed switch edge
```

These names should initially point at the safe lab front door rather than
direct private addresses unless a VPN/private resolver design explicitly owns
that split-horizon behavior.

Initial managed records should be declarative and conservative.

Later public records:

```text
auzietek.com          -> current Drupal, later beta/micro-blog cutover
www.auzietek.com      -> current Drupal, later beta/micro-blog cutover
beta.auzietek.com     -> micro-blog proving ground
kb.auzietek.com       -> Kanboard, private/protected policy
mon.auzietek.com      -> Grafana, private/protected policy
prom1.auzietek.com    -> Prometheus, private/protected or retired
dtlab.auzietek.com    -> deprecated Gogs/dtlabs, export then retire
clu.auzietek.com      -> experimental OpenWebUI, do not promote
clu-api.auzietek.com  -> experimental Ollama API, likely protect/retire
```

Future mail records:

```text
MX
SPF TXT
DKIM TXT/CNAME
DMARC TXT
Google verification TXT
```

Future VPN/remote-site records:

```text
ionos-auzietek-01.auzietek.com
ionos-auzietek-02.auzietek.com
vpn-ionos.auzietek.com
```

## Pipeline candidate

Future pipeline:

```text
ionos-lab-dns-api-inventory
```

Stages:

1. resolve `secret:ionos/dns/api`
2. list zones
3. snapshot `lab.auzietek.com` records
4. compare current records to desired lab edge map
5. produce proposed changeset
6. backfill missing stable `host.lab.auzietek.com` records
7. require explicit approval for writes
8. apply selected records
9. validate public DNS resolution
10. record graph fragments

Follow-up production pipeline:

```text
ionos-auzietek-dns-cutover
```

That follow-up should remain disabled until the beta site content migration,
rollback plan, SSL plan, and mail routing plan are ready.

Write stages must be explicit and reversible. DNS changes should include:

```text
old value
new value
TTL
reason
rollback value
validation command
```

## BKC graph relationships

Candidate objects:

```text
provider: ionos
zone: auzietek.com
zone-scope: lab.auzietek.com
secret: ionos/dns/api
record: bkc.lab.auzietek.com
record: edge.lab.auzietek.com
record: beta.auzietek.com
record: auzietek.com
service: micro-blog-beta
service: drupal-public-archive
service: kanboard-private
```

Useful relationships:

```text
dns_record_points_to
managed_by_provider
credential_used_by
public_name_for
cutover_candidate_for
```

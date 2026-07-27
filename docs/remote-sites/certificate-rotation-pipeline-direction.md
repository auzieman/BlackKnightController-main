# Certificate rotation pipeline direction

Status: candidate PowerCockpit feature

Date: 2026-07-27

## Why this matters

The public CA and service-certificate world is moving toward shorter lifetimes,
more automation, and less tolerance for hand-managed certificate drift.

That turns certificate rotation into a useful early BKC/PowerCockpit feature:
small enough to demonstrate clearly, painful enough that real operators care.

## Scope ladder

```text
lab.auzietek.com
  -> prove DNS-01, wildcard issuance, cert inventory, spool/reload/rollback

beta.auzietek.com
  -> prove public site cert rotation before production cutover

auzietek.com / www.auzietek.com
  -> production rotation after beta and rollback are proven

appliance/service certs
  -> Proxmox, Portainer, IPFire, switch, OpenStack/Horizon where useful
```

## Provider inputs

IONOS provides documented DNS and SSL APIs. Use their Swagger/OpenAPI docs as
the provider contract.

```text
IONOS DNS API
  -> DNS-01 challenge records
  -> host.lab.auzietek.com records
  -> public cutover records later

IONOS SSL API
  -> inventory provider-managed certificates
  -> optional provider-native SSL products
```

Let's Encrypt DNS-01 remains the preferred automation lane for BKC-controlled
certificates because it can be tested safely under `lab.auzietek.com`.

## Pipeline shape

```text
discover
  -> find cert files, provider certs, nginx/apache/service bindings

inventory
  -> subject, SANs, issuer, serial, not_before, not_after, days_remaining

plan
  -> decide renew/reissue/no-op, target services, rollback material

issue
  -> DNS-01 or provider SSL API

stage
  -> write cert/key to staging path with permissions

validate
  -> openssl x509, service config test, local TLS probe

swap
  -> atomic symlink/file replace where possible

reload
  -> nginx/service reload, not full restart unless required

verify
  -> public TLS probe, SAN match, expiry, chain

record
  -> graph fragment, Kanboard evidence, pipeline run ID

rollback
  -> restore previous cert/key and reload if verification fails
```

## Required evidence

```text
old certificate fingerprint
new certificate fingerprint
old/new not_after
SAN list
service bindings touched
validation commands
rollback path
pipeline run ID
Kanboard accepted card when outside lab scope
```

## Guardrails

```text
private keys
  -> never in Git, logs, Kanboard descriptions, screenshots, or public docs

lab certs
  -> may be tested freely through the lab lane

beta certs
  -> require beta-scoped accepted card

production certs
  -> require production-scoped accepted card and rollback evidence
```

## BKC graph objects

```text
certificate
certificate_authority
dns_challenge_record
tls_service_binding
rotation_plan
rollback_material
kanboard_card
pipeline_run
```


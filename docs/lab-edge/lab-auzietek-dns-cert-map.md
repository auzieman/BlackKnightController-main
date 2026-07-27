# lab.auzietek.com DNS and certificate map

Status: candidate, lab-safe DNS/API proving ground

Date: 2026-07-26

## Intent

Use `lab.auzietek.com` as the safe public DNS namespace for the home lab.

This gives BKC readable names for operator-facing services without touching the
production `auzietek.com` cutover path.

## Ownership model

```text
IONOS DNS API
  -> owns public DNS records

ns1
  -> runs DNS API helper and Certbot DNS-01 hooks
  -> stores/renews lab wildcard certificates
  -> can spool certs to edge/services later

swarm1 lab edge
  -> current browser-facing reverse proxy/front door

individual lab services
  -> eventually receive cert/key copies or route behind HTTPS edge vhosts
```

## Desired first DNS map

Source file:

```text
ops/lab-certs/lab-auzietek-map.json
```

Naming rule:

```text
canonical infrastructure
  -> proxmox01.lab.auzietek.com
  -> openstack01.lab.auzietek.com
  -> esx01.lab.auzietek.com
  -> ns1.lab.auzietek.com

platform swarm nodes
  -> swarm1-esx.lab.auzietek.com
  -> swarm1-os.lab.auzietek.com

human service entry points
  -> grafana.lab.auzietek.com
  -> bkc.lab.auzietek.com
  -> portainer.lab.auzietek.com
  -> microblog.lab.auzietek.com
```

Keep this bounded. `lab.auzietek.com` is safe, but it should not become a
graveyard of every temporary VM. Add names when they improve operator clarity,
pipeline evidence, or certificate/service routing.

Host naming rule:

```text
when BKC names a stable lab host
  -> publish host.lab.auzietek.com
  -> backfill missing DNS records before depending on the name
  -> record the DNS relationship in the resource graph
```

The backfill stage should compare inventory hostnames to DNS before creating
records. Temporary throwaway instances do not automatically receive public DNS.

Important names:

```text
lab.auzietek.com          A      192.168.1.15
swarm1.lab.auzietek.com   A      192.168.1.15
ns1.lab.auzietek.com      A      192.168.1.10
proxmox01.lab.auzietek.com A     192.168.1.9
pve1.lab.auzietek.com     CNAME  proxmox01.lab.auzietek.com
openstack01.lab.auzietek.com A   10.20.0.240
esx01.lab.auzietek.com    A      10.20.0.114

bkc.lab.auzietek.com      CNAME  swarm1.lab.auzietek.com
edge.lab.auzietek.com     CNAME  swarm1.lab.auzietek.com
grafana.lab.auzietek.com  CNAME  swarm1.lab.auzietek.com
portainer.lab.auzietek.com CNAME swarm1.lab.auzietek.com
openstack.lab.auzietek.com CNAME swarm1.lab.auzietek.com
proxmox.lab.auzietek.com  CNAME  swarm1.lab.auzietek.com
esxi.lab.auzietek.com     CNAME  swarm1.lab.auzietek.com
switch.lab.auzietek.com   CNAME  swarm1.lab.auzietek.com
openwebui.lab.auzietek.com CNAME swarm1.lab.auzietek.com
microblog.lab.auzietek.com CNAME swarm1.lab.auzietek.com

ipfire.lab.auzietek.com   A      192.168.1.82

swarm1-esx.lab.auzietek.com A     10.20.0.121
swarm1-os.lab.auzietek.com  A     10.20.0.230
```

`microblog.lab.auzietek.com` is the alpha instance. It is allowed to be rough
and rebuildable. `beta.auzietek.com` is the public proving ground, and
`auzietek.com` is the later production cutover.

The private RFC1918 answers are intentional for this lab. They are useful when
the operator workstation is on the home/lab side or connected through the
future VPN path.

## Certificate plan

First certificate:

```text
lab.auzietek.com
*.lab.auzietek.com
```

Method:

```text
Let's Encrypt DNS-01
IONOS DNS API
Certbot manual auth/cleanup hooks
ns1 as certificate workhorse
```

Runtime secret on ns1:

```text
/root/.secrets/ionos-api-key
```

That file should contain the combined IONOS key:

```text
<public-prefix>.<secret>
```

Never commit that file or value.

## Deployment commands

Install helper scripts to ns1:

```sh
ops/lab-certs/deploy-to-ns1.sh root@192.168.1.10
```

Read-only DNS snapshot:

```sh
ssh root@192.168.1.10 \
  'python3 /opt/bkc-lab-certs/ionos_dns.py --api-key-file /root/.secrets/ionos-api-key snapshot --scope lab.auzietek.com'
```

Apply desired lab DNS map, after reviewing the map:

```sh
ssh root@192.168.1.10 \
  'python3 /opt/bkc-lab-certs/ionos_dns.py --api-key-file /root/.secrets/ionos-api-key apply-map --map-file /opt/bkc-lab-certs/lab-auzietek-map.json --apply'
```

Request/renew the wildcard certificate:

```sh
ssh root@192.168.1.10 \
  'LETSENCRYPT_EMAIL=admin@auzietek.com /opt/bkc-lab-certs/request-lab-auzietek-cert.sh'
```

## Spooling model

Initial cert source:

```text
/etc/letsencrypt/live/lab.auzietek.com/fullchain.pem
/etc/letsencrypt/live/lab.auzietek.com/privkey.pem
```

Future spool targets:

```text
swarm1 lab-edge nginx secret/config
ns1 nginx
IPFire import if needed
Proxmox trusted cert if desired
Portainer/other edge services where useful
```

Do not over-distribute private keys. Prefer one HTTPS reverse proxy where it is
practical, and only copy certs to individual services that truly need native
TLS.

## Rotation posture

Certificate lifetimes and CA rules are tightening across the industry. Treat
certificates as rotating operational inventory, not one-time setup.

The lab should prove:

```text
discover certs
  -> inventory subject/SAN/issuer/not-after/current target

renew or reissue
  -> use DNS-01 or provider SSL API

stage
  -> place new cert/key beside the old material

validate
  -> nginx/service config test, local openssl check

swap
  -> reload service

verify
  -> public TLS check and graph evidence

rollback
  -> restore previous cert/key if validation fails
```

This pattern should later apply to `beta.auzietek.com`, then production
`auzietek.com`, and eventually lab appliances such as Proxmox, Portainer,
IPFire, and the managed switch where native TLS is useful.

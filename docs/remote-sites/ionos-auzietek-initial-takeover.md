# IONOS Auzietek initial takeover

Status: initial keyed reconnaissance, no destructive changes

Date: 2026-07-26

## Intent

Bring the public Auzietek IONOS hosts under BKC control from the new/OpenStack
BKC lane while preserving existing public services.

This is a remote-site takeover, not a rebuild. The near-term goal is to make the
existing services observable, documented, key-controlled, and ready for cleaner
DNS/certificate/service routing.

## Access model

BKC SSH key was installed for `root` on the reachable IONOS hosts:

```text
74.208.45.164
74.208.45.165
```

Do not commit root passwords or provider API keys.

Future secrets should be represented as secret references only:

```text
secret:ionos/auzietek/root
secret:ionos/dns/api
secret:auzietek/tls/provider
```

## Reachability notes

```text
74.208.45.164: SSH reachable
74.208.45.165: SSH, HTTP, HTTPS reachable
74.208.235.36: common ports appeared closed from the lab/workstation path
```

`74.208.235.36` needs confirmation from the provider firewall/security policy or
IONOS console before BKC assumes it is offline.

## Host facts

### 74.208.45.164

```text
OS: Ubuntu 22.04.4 LTS
kernel: 5.15.0-124-generic
docker: 26.0.0
swarm state: active worker
uptime: ~629 days at first recon
root filesystem: 233G, 99% used
```

Important: this host is critically low on disk. Treat cleanup/migration as a
first operational ticket before adding more workload.

Observed containers:

```text
grafana_grafana
kb_kanboard
ollama_open-webui
ollama_ollama
```

Observed working directories:

```text
/svc/grafana-compose
/svc/OTEL_Pythondemo
/svc/python_automation
/svc/etc/telegraf
/srv/grafana01
/srv/ollama
/srv/mysql_coffeeshop
```

### 74.208.45.165

```text
OS: Ubuntu 22.04.5 LTS
kernel: 5.15.0-101-generic
docker: 28.5.1
swarm state: active manager / leader
uptime: ~842 days at first recon
root filesystem: 233G, 28% used
```

Observed swarm services:

```text
auzietek_db          mysql:5.7
auzietek_drupal      drupal:9.5.11
dtlabs_dtlabs        gogs/gogs:0.12.10       deprecated
grafana_grafana      grafana/grafana:latest
grafana_mariadb      mariadb:latest
grafana_prometheus   prom/prometheus:latest
kb_kanboard          kanboard/kanboard:latest
ollama_ollama        ollama/ollama:latest
ollama_open-webui    dyrnq/open-webui:latest
```

Observed compose/service source folders:

```text
/svc/drupal_auzietek
/svc/dtlabs
/svc/grafana-compose
/svc/kb
/svc/micro-blog
/svc/nginx
/svc/ollama
/svc/ollama_tasks
/svc/ollama_worker
/svc/portainer
/svc/testing
```

Observed persistent data folders:

```text
/srv/drupal
/srv/mysql
/srv/gogs
/srv/grafana01
/srv/prometheus01
/srv/micro-blog
/srv/ollama
/srv/ollama_tasks
/srv/portainer
```

## Public service map

Nginx on `74.208.45.165` is the public front door.

Observed virtual hosts:

```text
auzietek.com          -> 74.208.45.165:8080  -> Drupal
www.auzietek.com      -> 74.208.45.165:8080  -> Drupal
ipv4.auzietek.com     -> 74.208.45.165:8080  -> Drupal
beta.auzietek.com     -> 127.0.0.1:18081     -> micro-blog UI
clu-api.auzietek.com  -> 74.208.45.165:11434 -> Ollama API
clu.auzietek.com      -> 74.208.45.165:5050  -> OpenWebUI
dtlab.auzietek.com    -> 74.208.45.165:8081  -> Gogs
mon.auzietek.com      -> 74.208.45.165:3301  -> Grafana
mon1.auzietek.com     -> 74.208.45.165:3301  -> Grafana
kb.auzietek.com       -> 74.208.45.165:8082  -> Kanboard
prom1.auzietek.com    -> 74.208.45.165:9090  -> Prometheus
```

## Swarm notes

The IONOS swarm currently has duplicate hostnames:

```text
74.208.45.165 -> docker node hostname: ubuntu -> manager/leader
74.208.45.164 -> docker node hostname: ubuntu -> worker
third node     -> docker node hostname: ubuntu -> down
```

This should be cleaned before BKC depends heavily on graph identities. Candidate
names:

```text
ionos-auzietek-01 -> 74.208.45.165
ionos-auzietek-02 -> 74.208.45.164
ionos-auzietek-03 -> unknown/down legacy node
```

## Direction

Near-term:

1. rotate old root passwords after BKC key access is proven
2. resolve/identify `74.208.235.36`
3. clean disk pressure on `74.208.45.164`
4. rename hosts / Docker nodes into stable identities
5. add BKC inventory records and remote-site graph edges
6. add Telegraf/node-exporter/Prometheus scrape route
7. put IONOS DNS and certificate automation behind secret refs
8. document nginx routes as managed edge objects

Medium-term:

- preserve Drupal/Grafana CLI access while BKC learns this site
- use micro-blog as the beta/public successor surface where appropriate
- treat dtlabs/Gogs as deprecated: preserve/export repositories and evidence,
  but do not modernize it as the future source-control system
- move source control, issues, and public collaboration flows to GitHub
- clean URLs and certificates using IONOS DNS automation plus Let's Encrypt or
  the selected commercial certificate provider
- add Kanboard/GitHub issue flow for operations drift
- add VPN/IPFire/WireGuard-style site connectivity so the public hosts become a
  proper remote BKC site rather than loose public VPSes

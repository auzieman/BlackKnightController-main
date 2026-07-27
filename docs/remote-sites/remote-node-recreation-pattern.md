# Remote node recreation pattern

Status: reusable power-cockpit pattern

Date: 2026-07-26

## Purpose

When BKC finds an older remote node, the goal is not to lovingly maintain the
snowflake forever. The goal is to sort it, preserve what matters, and recreate
the useful services as clean managed infrastructure.

This is the remote-site version of cattle-not-pets.

## Pattern

```text
discover
  -> can we reach it, what names point at it, what services answer?

inventory
  -> OS, disks, packages, Docker/Swarm, nginx, data paths, open ports

preserve
  -> archive data/configs/repos/db dumps outside Git

classify
  -> promote, migrate, archive, redirect-only, retire

rebuild
  -> create clean node or container stack from current templates

restore
  -> import only the classified useful payload

validate
  -> HTTP, DNS, TLS, logs, backups, monitoring, links

cut over
  -> DNS/proxy/NAT change with rollback

retire
  -> stop old service after backup and validation
```

## Evidence to capture

Minimum capture:

```text
hostnamectl / uname
ip route / ip addr
df -h / lsblk
docker ps -a
docker service ls
docker stack ls
nginx/apache vhosts
systemd unit list for local services
important /srv and /svc paths
database dumps when applicable
repo bare mirrors or Gogs/Gitea storage
```

## Classification

Use simple labels:

```text
promote
  useful and should become clean managed infrastructure

preserve-private
  valuable but not public

archive-public
  useful history, served read-only or imported as content

rewrite
  source material for a new article/tutorial/project

redirect-only
  keep URL/history but do not preserve runtime

retire
  safe to remove after backup window
```

## DNS and SSL

DNS and SSL changes happen after preservation and validation.

Safe lane:

```text
lab.auzietek.com
  -> prove DNS API and certificate automation

beta.auzietek.com
  -> prove public content/site shape

auzietek.com
  -> deliberate production promotion
```

## BKC objects

Candidate graph objects:

```text
remote_site
remote_node
public_dns_record
reverse_proxy
container_stack
repo_archive
content_archive
database_dump
backup_manifest
promotion_plan
```

Candidate relationships:

```text
dns_record_points_to
proxy_routes_to
container_uses_volume
archive_captured_from
repo_migrates_to
content_promotes_to
node_recreated_as
service_retired_by
```

## dtlabs example

`dtlab.auzietek.com` follows this pattern:

```text
discover
  -> nginx route and Gogs container found on IONOS edge

preserve
  -> /svc/dtlabs and /srv/gogs captured locally

classify
  -> BKC/AI/demo repos likely useful, older unrelated repos preserved only

rebuild
  -> GitHub becomes the long-term source-control home

retire
  -> dtlab public route can be removed after verification
```

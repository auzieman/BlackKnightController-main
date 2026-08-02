# dtlabs / Gogs preservation

Status: preserve-before-retire

Date: 2026-07-26

## Purpose

`dtlab.auzietek.com` is the deprecated Gogs/dtlabs surface on the IONOS edge,
but it may contain project history, early BlackKnightController work, and older
AI-assisted/codex-adjacent experiments.

Treat it as an archive source before retirement.

## Current known shape

From the IONOS takeover notes:

```text
public name: dtlab.auzietek.com
front door:  74.208.45.165 nginx
backend:     Gogs / dtlabs
container:   dtlabs_dtlabs
image:       gogs/gogs:0.12.10
likely dirs: /svc/dtlabs
             /srv/gogs
```

## Preservation stance

Do not delete or mutate the running service until the archive is captured and
verified.

Desired local backup target:

```text
~/Projects/auzietek/dtlabs/backups/<timestamp>/
```

The payload should stay outside this Git repository. Commit only manifests,
checksums, and notes.

## Capture checklist

1. Confirm SSH key auth to the IONOS edge host.
2. Record container/service metadata.
3. Archive likely Gogs data paths.
4. Pull archives locally.
5. Capture `docker inspect` output for matching containers.
6. Capture nginx vhost snippets referencing dtlab/Gogs.
7. Generate checksums.
8. Record a local manifest in this repo without repository contents or secrets.

## Captures

```text
20260727T033453Z
  host: 74.208.45.165
  local: ~/Projects/auzietek/dtlabs/backups/20260727T033453Z
  payload: dtlabs-paths.tgz
  extracted capture size: ~81 MB
  metadata: docker ps/service/inspect, candidate config path list, checksums

20260727T033605Z
  host: 74.208.45.164
  local: ~/Projects/auzietek/dtlabs/backups/20260727T033605Z
  payload: dtlabs-paths.tgz
  extracted capture size: ~616 KB
  metadata: docker ps/service/inspect, candidate config path list, checksums
```

Repository inventory captured from archive paths:

```text
74.208.45.165
  auzieman/ai_worker.git
  auzieman/amiwritermui.git
  auzieman/amiwriterreact.git
  auzieman/blackknightcontroller.git
  auzieman/container_tamer.git
  auzieman/muirc.git
  auzieman/ollama_tasks.git
  auzieman/otel_demo.git
  auzieman/requests_spider.git
  auzieman/selenium_bot.git

74.208.45.164
  auzieman/blackknightcontroller.git
  auzieman/container_tamer.git
  auzieman/ollama_tasks.git
  auzieman/otel_demo.git
  auzieman/requests_spider.git
  auzieman/selenium_bot.git
  fireplacesandstovesuk5906/5970938.git
  fireplacesandstovesuk5906/5970938.wiki.git
  sofasandcouchesuk9647/www.sofasandcouches.uk2998.git
  sofasandcouchesuk9647/www.sofasandcouches.uk2998.wiki.git
```

The older unrelated/commercial-looking repositories are preserved as evidence
but should not be promoted without manual review.

## After capture

Classify the contents:

```text
preserve-private
  -> keep local/archive only

promote-to-github
  -> migrate source repo publicly or privately

fold-into-bkc-docs
  -> extract useful fragments/examples

retire
  -> leave only redirect/archive notes
```

Likely outcome:

```text
Gogs/dtlabs service
  -> export/preserve
  -> retire public route
  -> GitHub becomes source control home
```

# MICROBLOG OPS 20 — Refresh ESXi Lab Canary From Repo Commit

This pipeline is the repeatable path for updating the ESXi lab micro-blog
preview after content, theme, or UI code changes are reviewed.

It deliberately avoids the bad pattern:

```text
ssh into a running container
  -> patch files by hand
  -> forget which edits were real
```

The intended pattern is:

```text
confirm lab and ESXi swarm health
  -> select repo commit / branch
  -> build a fresh container image
  -> push through the lab registry path
  -> replace the running Swarm service
  -> run content sync/adoption checks when needed
  -> validate links and proof strings
  -> write a receipt
```

## Current known-good canary

As of 2026-07-31:

```text
source repo: /home/auzieman/Projects/micro-blog
branch: review/astra-public-polish-20260731
commit: c8bdb4b
image: swarm1.lab.auzietek.com:5001/micro-blog/blog-ui:astra-polish-20260731-c8bdb4b
edge URL: http://swarm1.lab.auzietek.com:8091/
ESXi internal URL: http://10.20.0.121:18081/
```

## Scope

This is a lab canary refresh. It may:

- build a new `blog-ui` image from a selected repo commit;
- push the image into the lab registry;
- update `micro-blog_blog-ui` on the ESXi Docker Swarm;
- sync `/content` to node-local paths or shared storage;
- call the micro-blog filesystem bootstrap sync;
- validate canonical pages, proof strings, images, and health.

It must not:

- deploy to IONOS;
- change DNS;
- change certificates;
- promote beta to production;
- run destructive PXE or rebuild stages;
- patch files inside a running container as the normal path.

## Why this exists

During the content polish pass, a direct canary patch helped validate changes
quickly, but it also exposed a migration trap: mounted Markdown can import under
stable filesystem `source_id` records while an older public canonical slug still
serves a legacy database article.

The pipeline therefore validates content, not just HTTP status.

## Required proof strings

Default validation should confirm:

```text
/                         -> What can Auzietek do for you
/articles                 -> Field notes, walkthroughs, and proof-backed teaching material
/principles               -> Progressive trust and explicit boundaries
/aiops                    -> Bootstrap CLI versus controller
/business-case            -> Public benchmarks, carefully used
/friends                  -> Good infrastructure work is stronger
```

## Relationship to `micro-blog-swarm-compose`

Use `micro-blog-swarm-compose` when deploying the whole stack or proving the
substrate from scratch.

Use this pipeline when the stack already exists and the goal is to refresh the
lab preview with a new reviewed app/content state.


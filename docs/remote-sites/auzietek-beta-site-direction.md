# Auzietek beta site direction

Status: content/product direction for `beta.auzietek.com`

Date: 2026-07-26

## Purpose

`beta.auzietek.com` should become the proving ground for the future Auzietek
public site.

The goal is not to recreate Drupal. The goal is to preserve the useful spirit,
content, and public credibility of the current site while moving toward a more
professional, modern, tutorial-driven surface.

Long-term, beta/micro-blog becomes the new Auzietek public space and gradually
replaces the scattered legacy `*.auzietek.com` surfaces where appropriate.

Kanboard remains a private/semi-private operations and planning surface rather
than a public content engine.

Beta consolidation starts from a captured copy of Auzietek's current public
site state:

```text
Drupal SQL dump
Drupal files/assets
nginx/vhost evidence
legacy URL inventory
screenshots or public crawl evidence
```

Raw SQL and file captures stay outside Git. BKC/micro-blog should commit only
sanitized manifests, import tooling, classification notes, and promoted content
that has been reviewed.

## Operating ownership

Near-term BKC/Codex-owned surfaces:

```text
beta.auzietek.com
  -> proving-ground public site and micro-blog evolution

lab.auzietek.com
  -> safe DNS/API/certificate proving ground

kb.auzietek.com / Kanboard
  -> private and semi-public work tracker, issue staging, promotion checklist
```

Production `auzietek.com` becomes part of the same operating model only after
beta content, DNS, SSL, redirects, backups, and rollback are proven.

Rule of thumb:

```text
beta/lab/Kanboard
  -> build, test, iterate

auzietek.com
  -> promote deliberately from known-good beta state
```

## Promotion model

Micro-blog has three named environments:

```text
alpha
  -> lab.auzietek.com
  -> experimental, dogfood, rebuildable, allowed to be rough

beta
  -> beta.auzietek.com
  -> public proving ground, curated but still ahead of production

production
  -> auzietek.com / www.auzietek.com
  -> final public cutover after proof, redirects, SSL, backups, and rollback
```

Promotion path:

```text
micro-blog alpha in lab
  -> accepted in Kanboard for beta
  -> beta.auzietek.com
  -> accepted/released for production
  -> auzietek.com
```

Kanboard is the human intent gate. Moving a card to `accepted` means BKC may
consider the related promotion lane eligible.

Scope still matters:

```text
accepted for lab
  -> may change lab DNS, lab certs, alpha content, lab services

accepted for beta
  -> may publish to beta.auzietek.com or update public preview content

accepted for production
  -> may touch auzietek.com only when the card explicitly says production
```

No card status should silently broaden scope from lab to beta or from beta to
production.

## Audience

Primary audiences:

```text
young engineers
  -> practical walk-throughs, real examples, confidence-building explanations

technical peers
  -> architecture, evidence, automation patterns, honest tradeoffs

potential clients
  -> proof that Auzietek can reason through messy infrastructure and deliver
     working systems

AI / automation curious operators
  -> examples of prompt -> pipeline -> validated infrastructure
```

## Content posture

Auzietek content should be based on real use cases, not synthetic marketing
filler.

Core content types:

```text
tutorial
  step-by-step, reproducible, practical

walk-through
  guided explanation of a real build/fix/migration

case study
  problem, constraints, approach, result, evidence

lab note
  shorter operational finding or known-good fragment

concept
  higher-level thinking grounded by examples

project page
  serious overview of BKC, micro-blog, RX-demo, and related efforts
```

Every serious article should aim to answer:

```text
What problem was solved?
What environment did it happen in?
What tools/actions were used?
What evidence proves it worked?
What would a reader reuse?
What would we do differently next time?
```

## Theme

The site should feel professional, capable, and slightly futuristic without
looking like a toy dashboard.

Unlike BKC itself, `beta.auzietek.com` should lean lighter. BKC can keep the
dark-glass operations cockpit because operators live in it. The public site
should feel more open, readable, and editorial while still keeping an Auzietek
edge.

Desired feel:

```text
serious technical consultancy
lite mode with technical depth
slightly edgy, not gloomy
real lab evidence
advanced but readable HTML/CSS
structured depth where appropriate
clear typography
strong navigation
```

Avoid:

```text
generic blog theme
random CMS clutter
over-bright admin-dashboard styling
full BKC command-center darkness on public articles
AI hype without evidence
burying tutorials under personality text
```

Visual direction:

```text
background
  warm off-white / soft slate / subtle technical texture

cards
  glassy but readable, with rounded corners and restrained shadows

accents
  solarized-style blue, cyan, amber, and muted green

code/evidence blocks
  darker islands are fine, especially for commands, logs, and diagrams

diagrams
  can borrow BKC's graph language, but with more whitespace and simpler labels
```

## Navigation blueprint

Candidate top-level navigation:

```text
Home
Think Tank
Tutorials
Case Studies
BlackKnightController
Labs
About
Contact
```

Public URL consolidation direction:

```text
beta.auzietek.com
  -> proving ground / future public site

microblog.lab.auzietek.com
  -> alpha lab instance / infrastructure proof / dogfood

auzietek.com / www.auzietek.com
  -> eventual production cutover target once beta is reviewed

mon.auzietek.com / prom1.auzietek.com
  -> private or protected observability surfaces

kb.auzietek.com
  -> private Kanboard ops/work board

dtlab.auzietek.com
  -> deprecated Gogs/dtlabs; export/preserve then retire

clu.auzietek.com / clu-api.auzietek.com
  -> experimental AI endpoints; do not promote as primary public services
```

Optional deeper structure:

```text
Tutorials
  Linux / Systems
  Docker / Swarm
  OpenStack
  Proxmox / ESXi
  Monitoring / Observability
  AI-assisted Ops

Case Studies
  Bare-metal rebuild weekend
  Prompt-to-pipeline deployment
  OpenStack lab build
  ESXi swarm seed
  Public Auzietek migration

BlackKnightController
  Overview
  Demos
  Architecture
  Pipelines
  Resource Graph / Company Mind
  GitHub / Install

Labs
  Lab topology
  Hardware
  Network / VPN / IPFire
  Grafana / Portainer / Observability
```

## Drupal migration stance

Drupal is the current public archive and contains valuable articles/tutorials.
Micro-blog/beta should import the content value, not the Drupal engine.

Migration classes:

```text
cornerstone
  polish and promote

archive
  preserve with original date and redirect support

rewrite
  use as source material for a new serious version

redirect-only
  preserve URL value but do not feature
```

The first beta import should focus on a small curated set:

```text
Introducing BlackKnightController
Observable Cloud-Native Application with RX-Demo
Kubernetes / Drupal on k3s
Advanced Monitoring
Docker / Naming / Auto-tagging
Prompt-assisted systems automation pieces
```

## Advanced HTML concept direction

Use advanced web concepts to clarify, not decorate.

Good candidates:

```text
interactive architecture diagrams
copyable command blocks
collapsible evidence sections
timeline views for builds/migrations
before/after cards
resource graph embeds
callout panels for risk and lessons learned
status badges backed by real checks where possible
```

Example article structure:

```text
Hero
  title, summary, tags, difficulty, environment

Problem
  what broke / what needed building

Architecture
  diagram or compact topology

Walk-through
  steps, commands, config snippets

Evidence
  screenshots, pipeline IDs, Grafana/Portainer checks, logs

Reusable pattern
  what the reader can adapt

Follow-up
  known gaps, next work, GitHub issue/pipeline link
```

## BKC integration

The future site should be able to link public content to BKC evidence without
exposing private infrastructure details.

Candidate relationships:

```text
article -> pipeline
article -> lab run
article -> diagram
article -> GitHub repo
article -> public video
article -> sanitized known-good fragment
```

Private details stay in BKC/Kanboard. Public code/docs/issues go to GitHub.

## Mail stance

Avoid spending near-term energy on IONOS-hosted mail unless a hard requirement
appears. Running mail well is a time sink and a deliverability trap.

Preferred future direction:

```text
auzietek.com mail
  -> Google Workspace / Gmail-style provider
  -> DNS managed through IONOS API or future BKC DNS automation
```

BKC should track the required DNS records when that cutover happens:

```text
MX
SPF
DKIM
DMARC
verification TXT records
```

## Success criteria

The beta site is working when a new visitor can quickly understand:

```text
Auzietek solves real infrastructure problems.
BKC is a serious AI-assisted operations platform.
The tutorials come from proven lab/client-style work.
The author can guide engineers through messy systems clearly.
There is a path from article -> demo -> code -> evidence.
```

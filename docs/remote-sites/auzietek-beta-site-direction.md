# Auzietek beta site direction

Status: content/product direction for `beta.auzietek.com`

Date: 2026-07-26

## Purpose

`beta.auzietek.com` should become the proving ground for the future Auzietek
public site.

The goal is not to recreate Drupal. The goal is to preserve the useful spirit,
content, and public credibility of the current site while moving toward a more
professional, modern, tutorial-driven surface.

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

Desired feel:

```text
serious technical consultancy
operations command center
real lab evidence
advanced but readable HTML/CSS
dark glass / structured depth where appropriate
clear typography
strong navigation
```

Avoid:

```text
generic blog theme
random CMS clutter
over-bright admin-dashboard styling
AI hype without evidence
burying tutorials under personality text
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

## Success criteria

The beta site is working when a new visitor can quickly understand:

```text
Auzietek solves real infrastructure problems.
BKC is a serious AI-assisted operations platform.
The tutorials come from proven lab/client-style work.
The author can guide engineers through messy systems clearly.
There is a path from article -> demo -> code -> evidence.
```


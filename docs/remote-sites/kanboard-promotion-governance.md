# Kanboard promotion governance

Status: candidate governance rule

Date: 2026-07-26

## Purpose

Kanboard is the human intent gate for publishing, promotion, and controlled
remote-site changes.

The goal is to keep BKC powerful without making it spooky. A board movement
captures intent; BKC turns that intent into a scoped, evidenced action.

## Core rule

```text
Kanboard card moved to accepted
  -> related promotion lane becomes eligible
```

Eligible does not mean unlimited. It means the pipeline may proceed within the
scope stated by the card and the lane.

## Promotion lanes

```text
lab
  -> alpha systems, lab DNS, lab certs, internal services

beta
  -> beta.auzietek.com public preview, curated content, public demo updates

production
  -> auzietek.com / www.auzietek.com cutover, redirects, production SSL
```

## Micro-blog rule

```text
micro-blog alpha
  -> lab.auzietek.com

micro-blog beta
  -> beta.auzietek.com

micro-blog production
  -> auzietek.com / www.auzietek.com
```

Kanboard `accepted` can promote alpha to beta only when the card is scoped to
beta. Production requires an explicit production/release card.

## Required evidence

Promotion cards should collect links as BKC runs:

```text
pipeline run ID
commit SHA
DNS diff
cert validation
HTTP smoke result
browser smoke result when applicable
Grafana/Portainer evidence
rollback note
```

## Guardrails

```text
accepted for lab
  -> cannot touch auzietek.com production

accepted for beta
  -> cannot change production DNS/certs

accepted for production
  -> requires rollback and validation links

secret/cert material
  -> never copied into Kanboard descriptions, Git, or public content
```

## BKC graph relationship

Candidate objects:

```text
kanboard_card
promotion_lane
pipeline_run
site_environment
dns_record
certificate
rollback_plan
```

Candidate relationships:

```text
card_accepts
promotion_authorizes
pipeline_promotes
run_validates
dns_record_changes
certificate_serves
rollback_restores
```

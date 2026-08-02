# dtlabs recovered repo triage

Status: recovered locally, classify before promotion

Date: 2026-07-26

## Local recovery shelf

Working copies were recovered from the dtlabs/Gogs preservation archive into:

```text
~/Projects/auzietek/dtlabs/recovered-repos/
```

These are intentionally outside the BKC repository until each repo is reviewed,
sanitized, and either promoted to GitHub, folded into BKC, or archived.

## Recovered candidates

```text
ai_worker
container_tamer
ollama_tasks
otel_demo
requests_spider
selenium_bot
```

## Triage

### ollama_tasks

Priority: high

Why it matters:

```text
Ollama worker experiments
Kanboard integration
watchdog/file-event driven task model
script/template generation lineage
early BKC-adjacent automation pattern
```

Likely future use:

```text
PowerCockpit local AI worker
Kanboard issue/task bridge
prompt -> generated script -> run/evidence loop
repository memory for AI-assisted ops
```

Classification:

```text
preserve-private now
extract patterns into BKC later
possible cleaned GitHub example after review
```

### ai_worker

Priority: low/unknown

Current signal:

```text
README-only initial shell
```

Classification:

```text
preserve, revisit only if history reveals more
```

### container_tamer

Priority: high

Why it matters:

```text
early ancestor of BKC-style container control
config-driven container/script/template structure
Drupal example
Grafana example
Docker Compose era operational patterns
```

Observed shape:

```text
container_tamer.py
config.json
sample_config.json
templates/sample_config.conf
templates/sample_entrypoint.sh
examples/Drupal/
examples/Grafana/
```

This looks like the older cousin of the current BKC model: describe an
application/environment, render files/scripts, and run the resulting container
workflow.

Likely future use:

```text
historical lineage article
extract useful config/template vocabulary
compare old container_tamer -> current BKC pipeline model
recover Drupal/Grafana migration examples
```

Classification:

```text
preserve-private now
fold concepts into BKC docs/fragments
possible public historical repo after secret/config review
```

### requests_spider

Priority: medium-high

Why it matters:

```text
lightweight HTTP crawler
response-time collection
simple URL list driven checks
container-friendly pattern
```

This is dated, but the concept lines up with BKC's site smoke bot and edge
endpoint validation. It should not be copied directly; instead, salvage the
fast-check pattern.

Likely future use:

```text
BKC edge endpoint smoke checks
public-site link validation
beta.auzietek.com crawl before cutover
pipeline post-validation checks
```

Classification:

```text
extract pattern
modernize as requests/httpx/async optional
fold into BKC QA tooling or keep as small example repo
```

### selenium_bot

Priority: medium-high

Why it matters:

```text
browser-level synthetic checks
parallel/forked test runner
headless Chrome/Xvfb container pattern
Selenium IDE export workflow
```

This is also dated. Modern BKC should probably prefer Playwright for new
browser automation, but the repo still captures a useful model: heavy browser
checks only where HTTP-level checks are insufficient.

Likely future use:

```text
PowerCockpit browser QA lane
Horizon/Proxmox/Portainer login checks where APIs are not enough
public beta visual smoke tests
recorded operator journeys
```

Classification:

```text
extract pattern
modernize as Playwright-first, Selenium-compatible fallback
do not promote old CentOS/ChromeDriver instructions as current docs
```

### otel_demo

Priority: medium

Why it matters:

```text
OpenTelemetry demo lineage
collector/server/test split
compose-style deploy pattern
Dynatrace-oriented placeholders
```

Likely future use:

```text
observability tutorial material
small-office/demo telemetry examples
Grafana/Prometheus/Otel article source material
```

Classification:

```text
archive-public or rewrite
reuse concepts, not old vendor-specific placeholder config
```

## Suggested BKC follow-up

Create a PowerCockpit QA lane with two levels:

```text
fast spider
  -> HTTP status, latency, link crawl, simple content assertions

browser runner
  -> login/session/proxy/noVNC/Horizon/Portainer style checks
```

This should connect to:

```text
pipeline post-validation
resource graph node health
Grafana annotations
Kanboard task creation
public beta cutover checklist
```

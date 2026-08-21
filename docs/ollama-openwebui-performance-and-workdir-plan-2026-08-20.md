# Ollama + OpenWebUI Performance And Workdir Plan

Date: 2026-08-20

## Current observed state

OpenWebUI is running in the OpenStack Docker Swarm as:

```text
service: openwebui-os_openwebui
image: ghcr.io/open-webui/open-webui:main
published: 8088 -> 8080
placement: bkc-swarm-mgr-01
data volume: openwebui-os_openwebui_data -> /app/backend/data
ollama endpoint: http://10.20.0.130:11434
public/lab URL: https://openwebui.lab.auzietek.com
```

The live Ollama API on `10.20.0.130:11434` currently reports:

```text
qwen3.8:latest
  27.3B Q4_K_M
  ~17.7 GB
  256K context
  capabilities: completion, tools, thinking, vision

deepseek-v4-flash:cloud
  remote/cloud model shim
  1M context reported
  capabilities: completion, tools, thinking

qwen2.5-coder:1.5b
  ~986 MB
  32K context
  capabilities: completion, tools, insert
```

No models were loaded at the time of inspection.

## Operating rule

Ollama is a local receipt worker, summarizer, classifier, and draft reviewer.
It is not the source of truth for infrastructure state.

Source of truth remains:

```text
BKC pipelines
git commits/tags
package manifests
receipts
logs
deterministic probes
human approval
```

## Model roles

Keep roles explicit so OpenWebUI, ai_worker, and BKC do not accidentally ask the
wrong model to do the wrong job.

```text
fast_receipts:
  preferred: qwen2.5-coder:1.5b
  use: log summaries, small script reviews, pipeline status narration

deep_local_reasoning:
  preferred: qwen3.8:latest
  use: package receipt triage, architecture notes, multi-file reasoning

cloud_deep_reasoning:
  preferred: deepseek-v4-flash:cloud
  use: explicit long-context review only, never secrets or unsanitized dumps

embeddings:
  candidate: nomic-embed-text or nomic-embed-text-v2-moe
  use: OpenWebUI document/RAG indexing and bkc-channel retrieval

code_midweight_candidate:
  candidate: qwen3-coder:30b or gpt-oss:20b
  use: compare against qwen3.8 for BKC/AUZiX build reasoning
```

## Recommended model pulls

Near-term useful pulls:

```bash
ollama pull nomic-embed-text
ollama pull llama3.2:3b
ollama pull qwen2.5-coder:7b
```

Candidate heavier pulls, only after checking disk and VRAM behavior:

```bash
ollama pull gpt-oss:20b
ollama pull qwen3-coder:30b
```

Do not casually pull very large local models such as `qwen3-coder:480b`; current
Ollama library metadata indicates that class needs hundreds of GB of memory.

## Performance knobs to validate

Suggested Ollama service environment for the R730 AI worker:

```text
OLLAMA_HOST=0.0.0.0:11434
OLLAMA_KEEP_ALIVE=30m
OLLAMA_NUM_PARALLEL=2
OLLAMA_MAX_LOADED_MODELS=2
OLLAMA_FLASH_ATTENTION=1
```

Why:

- `KEEP_ALIVE` avoids cold-loading the same model every time OpenWebUI or BKC
  asks for a receipt summary.
- `NUM_PARALLEL=2` keeps the UI and pipeline worker responsive without letting
  every request fight for GPU/CPU at once.
- `MAX_LOADED_MODELS=2` is realistic with an 8 GB GPU plus CPU/RAM fallback.
- Flash attention should be benchmarked, not assumed.

Benchmark each change with:

```bash
curl -fsS http://10.20.0.130:11434/api/ps
curl -fsS http://10.20.0.130:11434/api/tags
```

and one timed prompt per model role.

## OpenWebUI workdir / document loading

Back-load work files into OpenWebUI deliberately. Do not dump entire repos with
secrets, `.git`, `.env`, private keys, certificates, or raw service configs.

Good material:

```text
bkc-channel notes and receipts
AUZiX build receipts
AUZiX package manifests
BKC pipeline README files
BKC pipeline JSON
sanitized logs
draw.io / architecture notes
public website copy
```

Bad material:

```text
.env files
SSH configs containing private routing details
private keys
API tokens
certificate private keys
unsanitized Docker inspect dumps
raw browser/session data
whole git history by default
```

Suggested staging pattern:

```text
/srv/openwebui-drop/
  auzix/
    receipts/
    package-manifests/
    build-notes/
  bkc/
    pipelines/
    architecture/
    channel/
  public/
    auzietek/
    linux-users/
```

Use a BKC render-stage pipeline to create sanitized bundles from git HEAD:

```text
source git paths
  -> denylist secrets/noise
  -> copy selected files to sanitized staging
  -> produce manifest + checksums
  -> optionally import/upload to OpenWebUI knowledge/workspace
```

## ai_worker tuning direction

Current `ai_worker` code still treats Ollama mostly as one model behind
`OLLAMA_MODEL`. Next useful step is a small role router:

```json
{
  "ollama_roles": {
    "fast_receipts": "qwen2.5-coder:1.5b",
    "deep_local_reasoning": "qwen3.8:latest",
    "embedding": "nomic-embed-text",
    "cloud_review": "deepseek-v4-flash:cloud"
  }
}
```

Each caller should request a role, not a model string. BKC and ai_worker can then
change model assignment without rewriting task logic.

## Guardrail

If Ollama gives advice that is not backed by a receipt, log, manifest, or file
path, treat it as a hypothesis only.


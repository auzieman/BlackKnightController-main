# OpenWebUI on OpenStack Swarm

Runs OpenWebUI on the OpenStack Docker Swarm and points it at the native Ollama service on server1.

Why it lives here:

- the edge VM CPU profile is too old for the current OpenWebUI / NumPy image;
- server1 currently runs Ollama directly and does not need to host the UI container;
- the OpenStack swarm already has modern worker capacity and published service ingress.

Service path:

```text
browser
  -> edge nginx / openwebui.lab.auzietek.com
  -> OpenStack swarm ingress 10.20.0.232:8088
  -> openwebui container :8080
  -> Ollama on R730 AI worker 10.20.0.130:11434
```

## Backend tuning direction

This deployment is intended to act as the standby company-mind cockpit when
Codex is offline or intentionally out of the loop.

The backend should favor:

- fast default model selection;
- Ollama-backed RAG embeddings;
- small curated context caches;
- mounted sanitized context from ai_worker;
- API-importable model/knowledge configuration;
- no arbitrary server-side Functions unless reviewed and trusted.

Current intended model roles:

```text
fast/default: qwen2.5-coder:1.5b
deeper local review: qwen3.8:latest
cloud/explicit review: deepseek-v4-flash:cloud
embedding: nomic-embed-text:latest
```

OpenWebUI notes:

- `RAG_EMBEDDING_ENGINE=ollama`
- `RAG_OLLAMA_BASE_URL=http://10.20.0.130:11434`
- `RAG_EMBEDDING_MODEL=nomic-embed-text:latest`
- `RAG_TOP_K=5`
- `CHUNK_SIZE=900`
- `CHUNK_OVERLAP=120`

Important: many OpenWebUI environment variables are persisted internally as
ConfigVars after first launch. If the Admin UI already stored a value, changing
the stack environment may not take effect until updated through the Admin UI/API
or by intentionally using `ENABLE_PERSISTENT_CONFIG=False` for a controlled
reset pass.

## Context cache mount

The OpenWebUI service mounts a read-only context cache:

```text
/workspace/openwebui-context-cache
```

This should be produced by ai_worker from sanitized sources:

```text
bkc-channel
ai_worker docs/notes
BlackKnightController docs/pipelines
AUZiX notes/packages/recipes
company-mind-preseed
```

The cache is not automatically trusted as live state. It is a last-known
workspace snapshot that should cite git/BKC/Kanboard/bkc-channel evidence.

## Company mind workspace/source mounts

OpenWebUI also receives read-only source mounts so the backend can literally
stand inside the company-mind filesystem layout:

```text
/workspace/company-mind/source/bkc-channel
/workspace/company-mind/source/ai_worker
/workspace/company-mind/source/BlackKnightController
/workspace/company-mind/source/AUZiX
/workspace/company-mind/preseed
```

Default host-side source root:

```text
/var/lib/company-mind/source
```

Expected host-side shape:

```text
/var/lib/company-mind/source/
  bkc-channel/
  ai_worker/
  BlackKnightController/
  AUZiX/

/var/lib/company-mind/preseed/
  README.md
  current-handoff.md
  hardware-profile.md
```

These mounts are intentionally read-only in OpenWebUI. They let the service see
the repo/cache shape, but they should not be treated as automatic Knowledge/RAG.
The reliable RAG path is still:

```text
source mounts
  -> ai_worker sanitized cache refresh
  -> OpenWebUI file upload/process
  -> OpenWebUI Knowledge collection
  -> optional Workspace Model preset with that Knowledge attached
```

This split matters: raw mounts provide filesystem continuity, while Knowledge
provides searchable/retrievable context.

## Workspace setup target

In OpenWebUI, create a Workspace Knowledge collection such as:

```text
Company Mind Standby
```

Attach it to a Workspace Model preset, for example:

```text
Company Mind Standby — Local
```

Recommended preset behavior:

```text
Base model: qwen2.5-coder:1.5b
Pinned deeper model: qwen3.8:latest
Knowledge: Company Mind Standby
Instruction: answer from workspace/cache/git/BKC evidence; if live state is needed, say so.
```

Deploy:

```bash
docker stack deploy -c stack.yml openwebui-os
```

Smoke checks:

```bash
docker service ls --filter label=com.docker.stack.namespace=openwebui-os
curl -sS -o /dev/null -w '%{http_code}\n' http://10.20.0.232:8088/
curl -k -sS -o /dev/null -w '%{http_code}\n' https://openwebui.lab.auzietek.com/
```

Backend checks:

```bash
docker service inspect openwebui-os_openwebui --format '{{json .Spec.TaskTemplate.ContainerSpec.Env}}' | jq
curl -fsS http://10.20.0.130:11434/api/tags | jq
```

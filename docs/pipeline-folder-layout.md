# Pipeline Folder Layout

BKC should move repeatable work out of large Python dictionaries and into
pipeline-owned folders. The first goal is not a visual designer; it is a stable
place to keep the recipe, assets, checks, and notes for one operational lane.

## Target Layout

Each built-in pipeline gets one folder:

```text
pipelines/<pipeline-id>/
  README.md
  pipeline.json
  assets/
  checks/
  templates/
  outputs.example.json
```

The folder owns the whole lane:

- `pipeline.json`: ordered stages, action ids, inputs, gates, target selectors,
  and expected outputs.
- `assets/`: small static inputs used by the lane.
- `checks/`: reusable validation scripts or check definitions.
- `templates/`: rendered shell, YAML, service, or config templates.
- `outputs.example.json`: sample produced facts and relationships for tests and
  UI work.
- `README.md`: operator notes, known risks, links, and recovery hints.

Runtime dictionary state follows the same shape under the mounted dictionary
volume:

```text
dictionaries/pipelines/<Pipeline_Name>/
  README.md
  pipeline.json
  items/
    00-preflight.json
    10-build.json
    20-validate.json
```

That path is visible inside the running BKC containers as `/app/dictionaries`.
It is the right place for lab-local lane metadata that should be easy to inspect
from an editor without opening one giant JSON file.

## Resource Gates

Pipelines that build images, publish package repositories, install operating
systems, or deploy to VMs should declare preflight gates before any expensive or
destructive stage.

Minimum gates for AuziX lanes:

- source commit gate: expected `.auzix-commit` or explicit source ref
- workspace gate: required free bytes on the build workspace
- repository gate: writable publish target when a publish stage exists
- target disk gate: VM disk size and free space before install/deploy
- runtime gate: expected package receipts, finalizer, and installer commands
- validation gate: explicit post-run checks for permissions and network/browser
  state

The VM disk gate matters because a 4 GiB target can fail in ways that look like
permissions, package, or GUI regressions. A pipeline should fail early with a
plain storage error instead of letting later stages create misleading symptoms.

## Migration Rule

Do not migrate every pipeline at once. For each lane:

1. Create the pipeline folder.
2. Copy the current metadata and stages into `pipeline.json`.
3. Move long inline scripts into `templates/` or `checks/`.
4. Add preflight gates.
5. Teach the executor to load that one folder.
6. Leave the existing Python fallback until the folder-backed lane has tests.

This keeps each session small and gives BKC a repeatable path away from
workflow-specific executor code.

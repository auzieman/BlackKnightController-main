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
  memory/
  templates/
  outputs.example.json
```

The folder owns the whole lane:

- `pipeline.json`: ordered stages, action ids, inputs, gates, target selectors,
  and expected outputs.
- `assets/`: small static inputs used by the lane.
- `checks/`: reusable validation scripts or check definitions.
- `memory/`: compact operational traces, decisions, and known-good fragment
  receipts that should travel with the lane.
- `templates/`: rendered shell, YAML, service, or config templates.
- `outputs.example.json`: sample produced facts and relationships for tests and
  UI work.
- `README.md`: operator notes, known risks, links, and recovery hints.

## Operational Memory

Each serious pipeline should keep a small machine-readable memory folder beside
the recipe:

```text
pipelines/<pipeline-id>/memory/
  traces.jsonl
  fragments.json
```

`traces.jsonl` is append-only JSON Lines. Each line should be compact enough for
an assistant or retrieval tool to load with the pipeline context before changing
the lane:

```json
{"id":"trace-2026-07-22-server1-uefi-drift","date":"2026-07-22","status":"active-risk","question":"Why did a previously working Server1 Trixie PXE lane fall into GRUB rescue?","retrieved_context":["pipeline:baremetal-openstack-lab-prepare","run:d54be704-0e04-4a44-a4aa-bc618d6f80dc"],"evidence":["Redfish BIOS BootMode was Bios while defaults required Uefi","SOL showed legacy Broadcom UNDI PXE","Screen showed grub rescue after installer handoff"],"decision":"Treat firmware mode as drift and gate the pipeline before arming PXE.","selected_tools":["redfish","idrac-sol","bkc-pipeline-run"],"result":"UEFI restore remained outstanding; no partitioning recipe change is proven."}
```

`fragments.json` records reusable known-good pieces. Use stable IDs, paths,
proof run IDs, and SHA-256 digests when the digest matters. A fragment is not
known-good because it ran; it is known-good because the target reached its
validated end state.

The goal is grounding, not fine-tuning. A general model should be able to open a
pipeline folder and find stable names, evidence, decisions, resources, and the
permissions boundary before it starts proposing changes.

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

## Live Loading Contract

Folder-backed pipeline definitions are live catalog data, not application code.
BKC should pick up these edits on the next request without restarting the
running container:

- `pipelines/<pipeline-id>/pipeline.json`
- `pipelines/<pipeline-id>/defaults.json`
- `pipelines/<pipeline-id>/items/*.json`
- `dictionaries/pipelines/<Pipeline_Name>/pipeline.json`
- `dictionaries/pipelines/<Pipeline_Name>/dictionary.json`
- `dictionaries/pipelines/<Pipeline_Name>/items/*.json`

The pipeline page polls `/api/v1/pipelines/catalog-signature` and shows a
refresh notice when those files change. A BKC service restart should only be
needed for Python executor behavior, Flask routes, database migrations, frontend
assets, or other feature code changes.

Runtime folder pipelines override repository folder pipelines with the same
`id`, so lab-local edits can be tested without changing the Git-backed recipe.

Pipeline variables should follow explicit scope rules so portable recipes can be
reused with lab-local overrides. See
[`pipeline-dictionary-scope.md`](pipeline-dictionary-scope.md) for the proposed
dictionary layout and variable precedence.

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

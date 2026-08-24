# BKC repository housekeeping baseline — 2026-08-24

## Product and lab separation

- Generic BKC services, adapters, templates, pipeline engines, and reusable
  pipeline definitions belong on the product branch.
- Site-specific inventory, internal addresses, machine identities, and private
  deployment defaults belong on an explicit private `lab/*` branch when they
  need version history.
- Credentials, generated runtime state, database exports, and operator scratch
  are never committed. Use `.private/` or `lab-private/`, both ignored.
- A reusable pipeline may reference a private dictionary by schema; it must not
  embed the private dictionary's values in the public pipeline.

## Current branch state

- The active Company Mind beta branch contains local commits not yet pushed.
  Hold publication until its lab-specific content is separated or the remote
  repository visibility is explicitly confirmed.
- The old local `main` is behind `origin/main` and must not receive new work.
- `codex/auzix-media-package-lanes` and `main-ui-beta-merge` are unmerged and
  remain protected pending review.
- Temporary `/tmp/bkc-*` worktree administrative records were pruned on
  2026-08-24. Local temporary branches already contained in `origin/main` were
  removed; their commits remain in `origin/main`.

## Commit boundaries for the existing working tree

1. BKC runtime/catalog hot reload;
2. pipeline executor and job action implementation;
3. resource graph UI and diagram export;
4. lab power, routing, and private inventory split;
5. OpenStack/VMware/bare-metal pipelines;
6. OpenWebUI/Company Mind deployment;
7. public Auzietek publishing pipelines;
8. documentation and media assets.

Do not combine these into a single cleanup commit. Each boundary needs focused
validation and a statement of whether it targets the public product branch or
the private lab branch.

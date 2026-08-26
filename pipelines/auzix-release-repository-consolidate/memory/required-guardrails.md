# AUZiX 31 required guardrail snapshot

Sources reloaded before every repository consolidation run:

- `BlackKnightController/operator/session-bootstrap.policy.json`
- `BlackKnightController/docs/build-guardrails.md`
- `BlackKnightController/docs/engineering-guardrails-no-troubleshooting-spirals.md`
- `AuZiX/packages/session-bootstrap.policy.json`
- `AuZiX/packages/rebase-native-build.pipeline.json`
- `AuZiX/notes/auzix-beta-factory-recenter-2026-08-23.md`
- `AuZiX/notes/auzix-release-lane-guardrails-2026-08-21.md`
- `bkc-channel/notes/2026-08-03-auzix-validation-loop.md`

Run interpretation:

- A repository is an artifact catalog. It is not an install selection.
- Wish lists and VM reference inventories do not automatically become image
  manifests.
- Dependency closure is resolved for an explicit package-group/install
  selection before that transaction; it is not inferred from every package
  retained in the catalog.
- Missing or conflicting evidence stops the run. No live repair or implicit
  dependency discovery is promoted as a release result.
- Later container and HDD stages require receipts from the preceding stage.

This snapshot summarizes the bkc-channel validation loop; the AUZiX and BKC
files above remain authoritative.

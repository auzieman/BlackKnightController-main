# BKC Build Guardrails

These are working rules for future BKC changes. They are intentionally practical rather than aspirational.

## Pipeline Work

- Prefer adding reusable actions over adding workflow-specific executor code.
- New pipelines should be recipes of actions, templates, target selectors, and validations.
- Give each migrated pipeline its own `pipelines/<pipeline-id>/` folder with the recipe, assets, checks, templates, and operator notes together.
- Every stage should have a clear validation, not just a command that exits zero.
- Expensive or destructive stages should have explicit preflight gates for source refs, free space, publish targets, and target disk capacity.
- Stage output should record enough detail to debug without exposing secrets.
- Successful stages should produce durable facts or relationships when possible.
- Keep lab-specific addresses in inventory, pipeline inputs, or tenant config unless a built-in lab default is explicitly intended.

## Action Work

- Actions should declare target kinds, required credential scope, inputs, risk level, and expected outputs.
- Actions should run both as direct resource operations and as pipeline stages when practical.
- Prefer rendered templates for non-trivial shell scripts or manifests.
- Do not inline long scripts into Python unless the script is very small.
- Use exact checks for stateful operations. Example: validate an NFS mount with `findmnt -M`, not a broad path lookup.

## Resource Graph Work

- A resource can be a host, VM, API, repository, storage share, credential scope, action, pipeline, service, or capability block.
- Do not force unknown resources to become hosts too early.
- Relationships should be typed and constrained by source kind and target kind.
- The graph should show both durable relationships and recent action history.
- Raw inventory should remain accessible, but promoted facts should drive the summary view.

## UI Work

- Let operators start from the selected resource, then show relevant tabs and actions.
- Avoid creating another page for every feature when the feature belongs under a resource tab.
- Dense operational screens should prioritize state, relationships, likely actions, and recent failures.
- Keep long raw output under Inventory, Logs, or History rather than above the fold.
- Use the pipeline page for runbook selection and run history, not as the only operational surface.

## Refactor Work

- Split large files when a reusable boundary exists.
- Avoid refactors that only move code without reducing future pipeline/action complexity.
- Keep tests focused on executor dispatch, action validation, permission gates, and produced facts.
- Preserve existing operator paths while introducing the new action model.

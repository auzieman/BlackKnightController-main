# AuziX VM130 Deploy

This folder is the dictionary-backed view of the VM130 deployment lane.

The current executor still has the runtime implementation in Python, but this
folder gives the operator and editor a narrower place to reason about the lane:

- `pipeline.json` keeps the lane metadata and ordered stages.
- `items/` keeps gates, checks, and follow-up work as small JSON objects.

Use this path for BKC-visible runbook state before expanding the runtime loader.

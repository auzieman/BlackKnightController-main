# BKC Pipelines

This directory is the landing zone for repository-backed pipeline recipes.

Current runtime definitions still live in `services/pipeline_catalog.py` and
`services/pipeline_executor.py`. New or migrated lanes should use one folder per
pipeline so the recipe, assets, templates, checks, and notes stay together.

See `docs/pipeline-folder-layout.md` for the contract.

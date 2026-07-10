# BKC Pipelines

This directory is the landing zone for repository-backed pipeline recipes.

Current runtime definitions still live in `services/pipeline_catalog.py` and
`services/pipeline_executor.py`. New or migrated lanes should use one folder per
pipeline so the recipe, assets, templates, checks, and notes stay together.

See `docs/pipeline-folder-layout.md` for the contract.

## Promoted Examples

- `rx-demo-k3s-redeploy-from-git/`: commit-triggered rx-demo redeploy example
  with k3s rollout, CloudEvents generation, and Loki/Grafana validation.
- `ns1-provisioning-network-prepare/`: graph-driven scaffold for preparing an
  isolated ns1 provisioning NIC before DHCP/PXE is enabled.
- `ns1-provisioning-dhcp-prepare/`: guarded DHCP configuration scaffold for the
  isolated ns1 provisioning network.
- `ns1-trixie-pxe-smoke/`: review-first Debian Trixie netboot and disposable
  VMID 132 PXE smoke lane.
- `auzix-installed-root-recovery/`: scaffold for installed-root repair and
  validation gates.
- `bkc-resource-graph-ui-review/`: review lane for the Cytoscape Resource Graph
  canvas, filters, layout controls, context menu, and position persistence.

Repository-backed examples should stay sanitized. Use selectors, variables, and
integration references instead of lab-only hostnames, credentials, tokens, or
absolute paths unless the path is intentionally part of the product contract.

Mounted dictionary pipelines may copy or override these recipes for a live lab,
but the main repository examples should remain portable enough for tests,
documentation, and demos.

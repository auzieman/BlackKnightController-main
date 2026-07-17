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
- `baremetal-r630-pxe-validation/`: validation-first scaffold for managing a
  powered-off physical server through BMC, NIC/MAC, PXE image, and evidence.
- `baremetal-openstack-lab-prepare/`: delivery-day OpenStack track scaffold for
  physical host identity, PXE readiness, base OS enrollment, and installer
  handoff validation.
- `trixie-openstack-host-prepare/`: post-enrollment Debian Trixie host prep for
  Neutron/Open vSwitch packages, kernel networking settings, and validation
  evidence before the OpenStack installer provider runs.
- `trixie-openstack-package-smoke/`: lab-safe package/config smoke lane that can
  reuse or clone the foo.bar SuiteCRM Trixie VM to test OpenStack host-prep
  scripts without claiming bare-metal readiness.
- `openstack-lab-edge-network-prepare/`: review-first routed edge plan for the
  10.1.x OpenStack lab networks, split DNS, workstation routes, bastion,
  nginx proxy, and service migration targets.
- `baremetal-vmware-trial-prepare/`: delivery-day VMware evaluation track
  scaffold for operator-supplied installer media, ESXi-style boot intent, first
  boot validation, and optional vCenter registration.
- `small-office-foobar-reference/`: recipe-level `foo.bar` small-office demo
  scaffold for identity/storage, CRM, two Windows helpdesk workstations, and
  two Linux developer workstations.
- `small-office-foobar-app-vms/`: runnable SuiteCRM/Kanboard application VM
  shell lane that clones `foobar-crm-01` and `foobar-tickets-01` from the
  prepared Trixie base.
- `small-office-foobar-services/`: runnable identity/storage, CRM endpoint,
  and ticket endpoint service lane for the `foo.bar` small-office demo.
- `small-office-foobar-reset/`: safe reset scaffold for wiping only `foo.bar`
  demo targets and generated evidence while preserving shared lab services.

See `docs/small-office-foobar-example.md` for the packaged architecture,
credentials, validation evidence, and BKC SSH handoff pattern.

See `docs/hardware-arrival-openstack-vmware-plan.md` for the incoming hardware
OpenStack and VMware evaluation preparation tracks.

## FooBar Demo Links

- Pipeline list:
  `http://swarm1.lab.auzietek.com:5000/pipelines?q=small-office-foobar`
- Reference recipe:
  `http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=small-office-foobar-reference`
- App VM clone lane:
  `http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=small-office-foobar-app-vms`
- Service provisioning lane:
  `http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=small-office-foobar-services`
- Clean service provisioning run:
  `http://swarm1.lab.auzietek.com:5000/pipelines/42ec95f7-7414-4e29-826a-325cd632f4d9`
- Browser-facing service IPs:
  recorded by `configure-demo-lan`; use those `192.168.1.x` links from the
  workstation unless the `*.lab.foo.bar` names have been registered in DNS.
- Identity portal checkpoint:
  `http://192.168.1.244/phpldapadmin/`
- CRM checkpoint:
  `http://192.168.1.59/suitecrm/`
- Ticket checkpoint:
  `http://192.168.1.133/kanboard/`
- Trixie PXE validation run:
  `http://swarm1.lab.auzietek.com:5000/pipelines/12caa567-6119-4e53-97a4-4aa1acf97463`
- Windows PXE validation run:
  `http://swarm1.lab.auzietek.com:5000/pipelines/06c3157f-91dc-4a6f-819e-12a324b77759`
- FooBar Grafana lifecycle:
  `http://swarm1.lab.auzietek.com:3000/d/small-office-foobar/foobar-small-office-lifecycle?orgId=1&refresh=5s`
- FooBar Grafana kiosk playlist:
  `http://swarm1.lab.auzietek.com:3000/playlists/play/dfrsvccecc074d?kiosk`

Repository-backed examples should stay sanitized. Use selectors, variables, and
integration references instead of lab-only hostnames, credentials, tokens, or
absolute paths unless the path is intentionally part of the product contract.

Mounted dictionary pipelines may copy or override these recipes for a live lab,
but the main repository examples should remain portable enough for tests,
documentation, and demos.

# Small Office FooBar Reference

Recipe scaffold for a tiny startup environment under the fake `foo.bar`
tenant/domain. The goal is to show BKC taking declared infrastructure intent
through PXE install, post-install personalization, and validation evidence.

This recipe intentionally stays boring:

- default password placeholder: `changeme123`
- helpdesk users: `joe.user`, `jill.user`
- developer users: `bob.dev1`, `bob.dev2`
- identity/storage node: `foobar-id-01`
- CRM node: `foobar-crm-01`
- ticket board node: `foobar-tickets-01`
- Windows helpdesk workstations: `foobar-helpdesk-win-01`,
  `foobar-helpdesk-win-02`
- Linux developer workstations: `foobar-dev-linux-01`,
  `foobar-dev-linux-02`

The first implementation is a compositional control plane recipe. It reuses
the existing PXE and workstation-personalization lanes instead of duplicating
installer details. Later runtime dictionaries can bind these logical nodes to
real VMIDs, MAC addresses, hostnames, and Proxmox placement.

The recipe should produce evidence that is useful on video:

- LDAP users/groups declared and validated.
- SMB home roots declared and mounted by workstations.
- CRM/intranet HTTP health passes.
- Windows helpdesk nodes have browser, RustDesk, and SMB access.
- Linux developer nodes have MATE, Git/SSH, VS Code, RustDesk, and shared home
  access.
- Each stage emits a normalized lifecycle event for Grafana or the Resource
  Graph.

## Recording Links

- Grafana lifecycle dashboard:
  `http://swarm1.lab.auzietek.com:3000/d/small-office-foobar/foobar-small-office-lifecycle?orgId=1&refresh=5s`
- Grafana lifecycle kiosk:
  `http://swarm1.lab.auzietek.com:3000/d/small-office-foobar/foobar-small-office-lifecycle?orgId=1&refresh=5s&kiosk`
- Grafana playlist kiosk:
  `http://swarm1.lab.auzietek.com:3000/playlists/play/dfrsvccecc074d?kiosk`
- BKC FooBar pipeline list:
  `http://swarm1.lab.auzietek.com:5000/pipelines?q=small-office-foobar`
- BKC app VM clone lane:
  `http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=small-office-foobar-app-vms`
- Trixie PXE validation run:
  `http://swarm1.lab.auzietek.com:5000/pipelines/12caa567-6119-4e53-97a4-4aa1acf97463`
- Windows PXE validation run:
  `http://swarm1.lab.auzietek.com:5000/pipelines/06c3157f-91dc-4a6f-819e-12a324b77759`

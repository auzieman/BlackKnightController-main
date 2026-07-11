# Small Office FooBar Reference

Recipe scaffold for a tiny startup environment under the fake `foo.bar`
tenant/domain. The goal is to show BKC taking declared infrastructure intent
through PXE install, post-install personalization, and validation evidence.

This recipe intentionally stays boring:

- default password placeholder: `changeme123`
- helpdesk users: `joe.user`, `jill.user`
- developer users: `bob.dev1`, `bob.dev2`
- identity/storage node: `foobar-id-01`
- intranet/CRM node: `foobar-crm-01`
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

# Small Office FooBar Services

Runnable service layer for the `foo.bar` small-office demo.

This lane follows `small-office-foobar-app-vms`. It creates the identity/storage
VM from the prepared Trixie source, then provisions the current service layer:

- `foobar-id-01`: OpenLDAP, Samba homes, and a phpLDAPadmin placeholder.
- `foobar-crm-01`: Apache/PHP/MariaDB with a SuiteCRM placeholder endpoint.
- `foobar-tickets-01`: Apache/PHP/SQLite with a real Kanboard service.

SuiteCRM remains intentionally lightweight for the first demo loop. Kanboard is
small enough to install as a real service and gives the helpdesk workstation a
credible ticket board target without adding a long application configuration
loop.

## Links

- Pipeline list:
  `http://swarm1.lab.auzietek.com:5000/pipelines?q=small-office-foobar`
- Service lane:
  `http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=small-office-foobar-services`
- Clean service run:
  `http://swarm1.lab.auzietek.com:5000/pipelines/ea6853ca-a4fd-477d-ad67-28150368ae83`
- Grafana lifecycle:
  `http://swarm1.lab.auzietek.com:3000/d/small-office-foobar/foobar-small-office-lifecycle?orgId=1&refresh=5s`

## Demo Checkpoints

- Identity VM:
  `foobar-id-01.lab.foo.bar`
- Browser-facing service IPs:
  `configure-demo-lan` records the current `ens19` addresses for workstation
  checkpoints. The `*.lab.foo.bar` names are inventory labels unless DNS has
  been registered for the workstation network.
- LDAP seed stage:
  `install-identity-packages` -> `seed-ldap-directory`
- Shared homes stage:
  `configure-samba-homes`
- Identity portal:
  `http://192.168.1.244/phpldapadmin/`
- CRM placeholder:
  `http://192.168.1.59/suitecrm/`
- Kanboard:
  `http://192.168.1.133/kanboard/`
- Validation evidence:
  `validate-foobar-services` in run `ea6853ca-a4fd-477d-ad67-28150368ae83`

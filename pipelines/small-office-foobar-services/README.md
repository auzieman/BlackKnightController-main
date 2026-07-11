# Small Office FooBar Services

Runnable service layer for the `foo.bar` small-office demo.

This lane follows `small-office-foobar-app-vms`. It creates the identity/storage
VM from the prepared Trixie source, then provisions the current service layer:

- `foobar-id-01`: OpenLDAP, Samba homes, and a phpLDAPadmin placeholder.
- `foobar-crm-01`: Apache/PHP/MariaDB with a SuiteCRM placeholder endpoint.
- `foobar-tickets-01`: Apache/PHP/SQLite with a Kanboard placeholder endpoint.

The placeholders are intentional for the first demo loop. They give BKC clear
service targets, health checks, and relationships without blocking the recording
on full PHP application installers.

## Links

- Pipeline list:
  `http://swarm1.lab.auzietek.com:5000/pipelines?q=small-office-foobar`
- Service lane:
  `http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=small-office-foobar-services`
- Clean service run:
  `http://swarm1.lab.auzietek.com:5000/pipelines/83892bb0-0979-40b3-a2b0-1b0f7476e487`
- Grafana lifecycle:
  `http://swarm1.lab.auzietek.com:3000/d/small-office-foobar/foobar-small-office-lifecycle?orgId=1&refresh=5s`

## Demo Checkpoints

- Identity VM:
  `foobar-id-01.lab.foo.bar`
- LDAP seed stage:
  `install-identity-packages` -> `seed-ldap-directory`
- Shared homes stage:
  `configure-samba-homes`
- Identity portal:
  `http://foobar-id-01.lab.foo.bar/phpldapadmin/`
- CRM placeholder:
  `http://foobar-crm-01.lab.foo.bar/suitecrm/`
- Ticket placeholder:
  `http://foobar-tickets-01.lab.foo.bar/kanboard/`
- Validation evidence:
  `validate-foobar-services` in run `83892bb0-0979-40b3-a2b0-1b0f7476e487`

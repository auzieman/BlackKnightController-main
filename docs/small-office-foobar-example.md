# Small Office FooBar Example

This example packages the `foo.bar` small-office demo as a repeatable BKC
reference architecture. It demonstrates BKC managing the lifecycle before,
during, and after the point where conventional IT automation tools become
useful.

## Objective

Build a tiny startup environment from pipeline-managed resources:

- Identity and shared homes: OpenLDAP, Samba, phpLDAPadmin.
- CRM: SuiteCRM on Apache/PHP/MariaDB.
- Tickets: Kanboard on Apache/PHP/SQLite.
- Windows helpdesk workstation: PXE/WinPE install, BKC SSH firstboot, Chrome,
  LibreOffice, VS Code, RustDesk.
- Linux developer workstation: Debian Trixie, graphical desktop, browser,
  LibreOffice, VS Code.

## Key Pattern

BKC owns the handoff boundary where many automation systems usually need manual
glue:

1. PXE, iPXE, WinPE, or Debian netboot gets the operating system onto a node.
2. Firstboot installs the BKC SSH trust path.
3. BKC records generated bootstrap credentials as a handoff artifact.
4. Post-install personalization continues through `bkc-ssh`.
5. Pipelines normalize human-facing demo credentials separately from bootstrap
   credentials.
6. Validation stages prove services, applications, shares, and workstation
   tools are actually reachable.

This does not replace Ansible. It gives Ansible and similar tools a cleaner
place to start: after provisioning, identity, reachability, and credential
handoff are already tracked.

## Pipeline Order

Use the filtered UI:

`http://swarm1.lab.auzietek.com:5000/pipelines?q=small-office-foobar`

Recommended order:

1. `small-office-foobar-reference`
2. `small-office-foobar-app-vms`
3. `small-office-foobar-services`
4. `ns1-trixie-pxe-smoke`
5. `windows10-pxe-smoke`
6. `trixie-workstation-personalize`
7. `windows10-workstation-personalize`

The service lane currently provisions real phpLDAPadmin, SuiteCRM, and Kanboard.

## Demo Credentials

Repository examples use demo-safe local credentials only. Bootstrap secrets
generated during PXE should remain runtime artifacts, not committed values.

- LDAP admin DN: `cn=admin,dc=foo,dc=bar`
- LDAP demo password: `changeme123`
- Trixie workstation: `auzieman` / `changeme123`
- Windows workstation: `depadmin` / `changeme123`
- SuiteCRM installer database: `localhost` / `suitecrm` / `suitecrm` /
  `changeme123`
- Kanboard initial login: `admin` / `admin`

The Windows PXE lane may generate a stronger one-time bootstrap password and
stage it on ns1 as `/root/bkc-vm<vmid>-credentials.txt`. The Windows
personalization lane then normalizes the console login to the demo credential.

## Checkpoints

Current lab URLs are recorded by pipeline evidence and should be refreshed from
the latest run when the VM addresses change.

- phpLDAPadmin: `http://192.168.1.244/phpldapadmin/`
- SuiteCRM: `http://192.168.1.59/suitecrm/`
- Kanboard: `http://192.168.1.133/kanboard/`
- Service run: `http://swarm1.lab.auzietek.com:5000/pipelines/42ec95f7-7414-4e29-826a-325cd632f4d9`
- Windows PXE run: `http://swarm1.lab.auzietek.com:5000/pipelines/06c3157f-91dc-4a6f-819e-12a324b77759`
- Grafana lifecycle: `http://swarm1.lab.auzietek.com:3000/d/small-office-foobar/foobar-small-office-lifecycle?orgId=1&refresh=5s`

## Validation Evidence

Important green-light stages:

- `publish-identity-portal`: phpLDAPadmin is configured against local OpenLDAP.
- `provision-suitecrm-service`: SuiteCRM release archive is deployed and
  Apache/MariaDB are running.
- `provision-kanboard-service`: Kanboard release archive is deployed and Apache
  is running.
- `validate-foobar-services`: identity, CRM, and ticket endpoints respond.
- `normalize-local-login`: workstation demo console login is known and
  repeatable.
- `verify-windows-personality` and `verify-trixie-personality`: workstation
  packages and remoting are present.

## Packaging Notes

Keep the repository examples portable:

- Store lab-local hostnames, generated credentials, and temporary IPs in runtime
  dictionaries or run evidence.
- Keep repository defaults safe and explainable.
- Prefer pipeline actions and BKC APIs over direct shell repair.
- If a manual repair is required, backfill it into the pipeline before treating
  the demo as stable.

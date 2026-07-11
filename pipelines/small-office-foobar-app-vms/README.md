# Small Office FooBar App VMs

Clone two Debian/Trixie application VM shells from the prepared Trixie base:

- `foobar-crm-01` for SuiteCRM
- `foobar-tickets-01` for Kanboard

This pipeline is the bridge between bare OS provisioning and business application
deployment. It does not install SuiteCRM or Kanboard yet; it creates stable VM
targets that the next app provisioning lane can take over.

## Recording Links

- Pipeline list:
  `http://swarm1.lab.auzietek.com:5000/pipelines?q=small-office-foobar`
- This lane:
  `http://swarm1.lab.auzietek.com:5000/pipelines?pipeline=small-office-foobar-app-vms`
- Trixie PXE validation run:
  `http://swarm1.lab.auzietek.com:5000/pipelines/12caa567-6119-4e53-97a4-4aa1acf97463`
- Windows PXE validation run:
  `http://swarm1.lab.auzietek.com:5000/pipelines/06c3157f-91dc-4a6f-819e-12a324b77759`
- Grafana lifecycle:
  `http://swarm1.lab.auzietek.com:3000/d/small-office-foobar/foobar-small-office-lifecycle?orgId=1&refresh=5s`

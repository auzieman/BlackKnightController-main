# Small Office FooBar App VMs

Clone two Debian/Trixie application VM shells from the prepared Trixie base:

- `foobar-crm-01` for SuiteCRM
- `foobar-tickets-01` for Kanboard

This pipeline is the bridge between bare OS provisioning and business application
deployment. It does not install SuiteCRM or Kanboard yet; it creates stable VM
targets that the next app provisioning lane can take over.

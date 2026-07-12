# ns1 Provisioning DHCP Prepare

Prepare DHCP service configuration for the isolated provisioning network on
`ns1` without enabling the service by default.

This pipeline follows `ns1-provisioning-network-prepare`. It assumes `ns1`
already has a management interface on `192.168.1.0/24` and a provisioning
interface on `10.20.0.0/24`.

## Guardrails

- Bind DHCP only to the provisioning interface.
- Never serve DHCP on the Spectrum-managed management network.
- Render and syntax-check configuration before service changes.
- Keep `enable_dhcp_service` false until an operator explicitly promotes the
  lane from prepare to serve.
- Record the DHCP service as a relationship on the provisioning network.

## First Run

The first run should validate:

- target host resolves to `ns1.lab.auzietek.com`
- provisioning interface is `ens19`
- DHCP package choice is present
- rendered config contains only the `10.20.0.0/24` subnet
- service is disabled unless the runtime dictionary opts in

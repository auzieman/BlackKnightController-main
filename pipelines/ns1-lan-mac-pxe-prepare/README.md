# ns1 LAN MAC PXE Prepare

Emergency bring-up lane for serving PXE from ns1 on the LAN-facing interface
for one explicitly allowed physical MAC address.

This is not the preferred long-term PXE topology. The preferred topology is an
isolated provisioning VLAN/network. This lane exists for first hardware
bring-up when the physical switch and ns1 isolated cabling are not settled yet.

Guardrails:

- no general DHCP range
- no `authoritative` DHCP behavior on the home LAN
- `deny unknown-clients`
- one fixed MAC/IP reservation
- DHCP binds to the declared LAN-facing interface only
- the recipe is review-first until the operator explicitly enables it

Default target MAC:

```text
80:18:44:de:fd:38
```

Confirm this is the server PXE NIC MAC before running the live mutation.

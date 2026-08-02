# N3048 Switch Discovery Prepare

Review-first discovery lane for the Dell PowerConnect N3048 managed switch.

This recipe is intentionally read-only. It declares the switch, management
endpoint, VLAN/port intent, and the observations BKC should collect before the
switch is used as the authoritative PXE evidence source.

The first runnable provider should collect:

- management reachability
- model/firmware
- interface list
- VLAN summary
- LLDP/CDP neighbors when available
- MAC address table
- port-to-node evidence for R630 NICs and ns1 uplinks

Do not push switch configuration until we have a known backup/export path and
out-of-band recovery access.

# ns1 Provisioning Network Prepare

This pipeline prepares ns1 to become the first BKC-managed provisioning network
controller without enabling DHCP or PXE yet.

The narrow goal is to establish and validate an isolated provisioning interface
for a future Debian/Fedora netboot lane.

## Guardrails

- Do not manage DHCP on `192.168.1.0/24`.
- Do not change the default route on ns1.
- Do not bind DHCP, TFTP, HTTP boot, or PXE services yet.
- Do not assume the provisioning interface name until discovery confirms it.
- Keep ns1 reachable through the management network during every stage.

## Intended Scope

The first target is ns1 because it is a VM, already acts as the Ansible
controller, and is already represented in BKC inventory and Proxmox discovery.

Later procedures can add the same isolated provisioning network to Docker Swarm
hosts, k3s nodes, and future AuziX/RND targets.

## Produced Evidence

The run should eventually record:

- current interface list
- current route table
- selected provisioning interface
- configured provisioning address
- management route validation
- relationship updates from ns1 to the provisioning network


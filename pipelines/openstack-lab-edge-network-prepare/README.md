# OpenStack Lab Edge Network Prepare

Review-first recipe for the routed edge of the OpenStack lab.

This pipeline declares the lab DNS suffix, 10.1.x service and management
subnets, workstation route intent, bastion placement, and nginx reverse proxy
placement. It should not mutate routes until ns1 interface/VLAN facts are
confirmed after the new hardware and switch are installed.

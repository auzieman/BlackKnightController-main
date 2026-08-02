# Bare-Metal Video Run Order

Use the `VIDEO` filter in the BKC pipeline view. The numbered names are the
operator sequence and deliberately sort to the top of the list.

## Before recording

1. Confirm `lab-edge_nginx` is `1/1` in the Swarm.
2. Open `http://swarm1.lab.auzietek.com:8080/` and validate every UI tile.
3. Confirm ns1 DHCP is active and the Server2 Proxmox one-shot fragment is
   empty/disarmed.
4. Confirm both iDRAC endpoints answer from ns1.
5. Archive the successful current-state evidence before destructive actions.
6. Confirm every discovery made through direct SSH, Redfish, console access,
   or a tunnel has been converted into the pipeline's executable action,
   guard, parameter, or validation evidence. A manual success is diagnostic
   evidence, not recording readiness.

## Workflow contract

The operating path is `prompt -> Codex -> BKC pipeline`. Codex may use
`bkc-ssh`, Redfish, or a tunnel to investigate a fault, but those transports do
not replace the pipeline. Before a filming lane is called ready, all necessary
findings and commands must be represented by the checked-in pipeline and run
by its worker. Do not rely on an uncaptured interactive repair or defer known
pipeline work until recording begins.

## Recorded sequence

1. `00 VIDEO — Bare Metal Lab Reset / Preflight`
2. `10 VIDEO — Server1 Bare Metal + OpenStack`
3. `20 VIDEO — Server2 Bare Metal + Proxmox`
4. `30 VIDEO — OpenStack Seed + Validate`

The Proxmox install boundary is:

1. arm the MAC-scoped DHCP/iPXE route;
2. set PXE-once and power-cycle through iDRAC;
3. wait for the embedded ISO auto-installer to report success;
4. disarm the DHCP include;
5. set hard-disk boot and power-cycle;
6. validate SSH, `/dev/kvm`, API authentication, and HTTPS port 8006.

Do not leave the MAC-scoped installer fragment armed after the installer
reboots. The parent `dhcpd.conf` includes the fragment by filename, so disarm
it by installing an empty file at the same path rather than deleting it.

## Operator edge URLs

- Landing page: `http://swarm1.lab.auzietek.com:8080/`
- Proxmox: `http://swarm1.lab.auzietek.com:8081/`
- Horizon: `http://swarm1.lab.auzietek.com:8082/`
- Dell switch: `http://swarm1.lab.auzietek.com:8083/`
- BlackKnightController: `http://swarm1.lab.auzietek.com:8084/`
- Grafana: `http://swarm1.lab.auzietek.com:8085/`
- Portainer: `http://swarm1.lab.auzietek.com:8086/`

## One-shot parent readiness

A future guarded parent pipeline may call the four stages above only after the
Proxmox folder pipeline is registered with the runtime executor and installer
completion/disarm is represented as a real callback or polling boundary. Until
then, keep the four operator calls explicit for a reliable recording.
PXE is deny-by-default. The broad DHCP fragments may issue leases but must not
contain `next-server`, `filename`, or `bootfile-name`. A deployment or diagnostic
pipeline owns the whole exception lifecycle: install an exact-MAC fragment,
validate and arm it, observe fresh HTTP handoff evidence for that target, then
empty the fragment and restart DHCP. A stray PXE request must never select an
installer or diagnostic image.

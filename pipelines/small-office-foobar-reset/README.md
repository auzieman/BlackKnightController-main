# Small Office FooBar Reset

Safe reset scaffold for the `foo.bar` demo environment.

This lane is scoped to demo-owned objects only:

- `foobar-id-01`
- `foobar-crm-01`
- `foobar-helpdesk-win-01`
- `foobar-helpdesk-win-02`
- `foobar-dev-linux-01`
- `foobar-dev-linux-02`
- generated `foo.bar` PXE/DHCP one-shot routes
- generated `foo.bar` validation evidence

It must not remove global ns1 provisioning services, cached Debian netboot
assets, cached Windows install media, BKC runtime state, Grafana, Loki,
Prometheus, registry content, or unrelated VM inventory.

The repository version is review-only by default. A live lab dictionary must
set `enable_destroy=true` and bind VMIDs/MAC addresses before this becomes a
destructive reset lane.

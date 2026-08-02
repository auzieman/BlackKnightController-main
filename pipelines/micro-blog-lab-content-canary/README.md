# Micro Blog Lab Content Canary

Content-only lab lane for public article/site payloads.

This pipeline assumes the ESXi lab micro-blog runtime already exists. Use
`micro-blog-swarm-compose` to build or converge the base stack. Use
`micro-blog-esxi-lab-canary-refresh` only when app code, theme code, or runtime
image content changed.

For ordinary article work, this is the smaller and safer path:

```text
approved micro-blog content tree
  -> copy markdown/assets to the lab swarm content mount
  -> call /admin/bootstrap/filesystem-sync
  -> validate lane URLs and proof strings
  -> record a receipt
```

This pipeline must not:

- build a new image;
- update a Docker Swarm service;
- redeploy the stack;
- reset/delete imported content by default;
- publish to IONOS/public domains.
- rediscover or replace existing lab edge/IPFire/Portainer tunnel paths unless
  the health gate proves the known-good path is actually down.

The important contract is inherited from `micro-blog/docs/micro-blog-pattern.md`:

```text
code changes rebuild images
content changes sync/import content
```

The lab is still where we can be bold, but the bold move here is repeatability:
article edits should be boring, visible, and re-runnable.

## Known-good transport posture

Do not start from scratch when operating this lane. The lab already has working
transport patterns:

- `bkc.lab.auzietek.com` is the OpenStack BKC control plane.
- ESXi swarm app traffic is exposed through the lab edge path.
- Portainer is a lab helper/control-plane diagnostic, not a public IONOS
  deployment dependency.
- IPFire/lab-edge tunnels may already expose internal services; prefer checking
  them before inventing a new route.

If one of those paths fails, record the failed health check first. Then repair
that path or use the existing fallback deliberately.

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

The important contract is inherited from `micro-blog/docs/micro-blog-pattern.md`:

```text
code changes rebuild images
content changes sync/import content
```

The lab is still where we can be bold, but the bold move here is repeatability:
article edits should be boring, visible, and re-runnable.

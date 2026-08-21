# BKC 02 — Runtime Git Sync + Catalog Refresh

Fast-forward the running BKC checkout from git and prove the filesystem-backed
pipeline/dictionary catalog sees the new state.

Intent:

- no app rebuild for pipeline or dictionary edits;
- no NFS mystery state as the normal path;
- worker/runtime nodes keep localized git checkouts;
- commits to `pipelines/` or `dictionaries/` become visible after a pull and
  catalog refresh/check.

This is deliberately conservative: maintain a localized git checkout under
`/srv/bkc/git/BlackKnightController`, fast-forward only, validate JSON, sync hot
pipeline folders into `/srv/bkc/runtime/pipelines`, update the mounted
`pipeline_executor.py` overlay, then print the live catalog signature.

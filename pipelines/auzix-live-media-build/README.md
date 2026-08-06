# AuziX preserved live recovery build

This BKC-visible workflow derives a recovery ISO from the pinned, runtime-tested
AuziX live artifact. It does not invoke the broad root builder or rebuild the
Docker tool image.

Pinned inputs:

- AuziX source commit: `3a08d8c`
- Base ISO SHA-256: `dbc37d309059b70cc39e37b7a5e0be7d27dae770654bf3ccf7ddf7d142c25cb6`
- Base SquashFS SHA-256: `7e2cc1a249e76c2711dd3659fc5485637229e9afd158584b6c937104ed37220a`

The worker reads committed source and staged-root inputs from ns1 NFS, applies
the bounded SSH and InstallerEFL deltas, preserves kernel `6.1.0-48` and the
existing boot map, and publishes a receipt for VM135 validation.

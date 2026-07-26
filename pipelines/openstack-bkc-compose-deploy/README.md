# OpenStack BKC Compose Deploy

This is the first “eat our own dogfood” deployment lane for BKC.

It intentionally mimics what a regular operator would do:

1. SSH to a Debian VM in OpenStack.
2. Mount ns1’s shared BKC runtime over NFS.
3. Clone/update the BKC source checkout.
4. Copy a local `.env` file that is never committed to Git.
5. Render a normal Docker Compose file.
6. Run `docker compose up -d --build`.
7. Validate `/ready`.
8. Publish the edge pointer for the new controller.

The pipeline keeps the first hop simple on purpose. Docker Swarm can come later
once the single-VM Compose deployment is repeatable.

## Required run inputs

- `target_host`: OpenStack VM IP or DNS name reachable from BKC/inside lab.
- `bkc_secret_key`: generated with `openssl rand -hex 32`.
- `bootstrap_admin_password`: first-login password; remove it from the target
  `.env` after the admin account exists.

## Secret boundary

The committed defaults and templates describe the shape. The actual rendered
`/srv/bkc/.env` lives only on the target VM and should not be copied back into
Git.

## Edge pointer

The initial edge mode is `record-only`. It records the intended proxy/NAT edge
URL and validated backend.

Default label:

```text
BlackKnightController — OpenStack
```

Default edge slot:

```text
http://swarm1.lab.auzietek.com:8090/
```

That edge should proxy or NAT to:

```text
http://<target_host>:5000/
```

Do not replace the old/current BKC route while it is still acting as the launch
controller. Promote the new route only after the OpenStack-side BKC validates.

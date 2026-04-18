#Black Knight Controller

Black Knight Controller is a web-based interface for managing a Fabric-based deployment system. The name is a nod to the urban legend of an ancient alien craft orbiting the Earth, which some people believe is recording and guiding humanity. 

*However, please note that this project is purely fictional and not based on any factual evidence. And definatly not an indication that 42 is the ultimate answer. Also though this concept may seem alien its just a human and some AI working together :) *

The initial functionality of the Black Knight Controller includes the ability to manage groups and hosts, edit group and host information, and deploy code to specific hosts. The interface is designed with a left navigation menu for easy access to the different sections of the application. The theme is rounded and blue, with a modern look and feel.

In addition to the initial functionality, the Black Knight Controller also includes an "Add Nodes" routine. This allows users to add a list of hosts (IP or hostname) to a specific group. The system will test the credentials and save them, or perform a round of interactive steps to copy a preshared key to the destination.

We hope that the Black Knight Controller will help simplify the deployment process and make it more accessible for users of all levels. Please feel free to use, modify, and contribute to this project as you see fit.

Key feature list.

## View and edit configuration files for fabric deployments
- Add, remove, and edit groups of servers to deploy to
- Add, remove, and edit individual servers within a group
- View and edit templates for fabric deployments
- Deploy to individual servers or groups of servers with fabric
- Ability to add new nodes to an existing group with form validation and handling
- User-friendly UI with a modern and clean theme

We hope that the Black Knight Controller will help simplify the deployment process and make it more accessible for users of all levels. Please feel free to use, modify, and contribute to this project as you see fit.

To ensure you have the right python libraries run your OS's version of this command.

`pip install -r requirements.txt`

### Automated tests

Install dev tools with `pip install -r requirements.txt -r requirements-dev.txt`, then run **`ruff check .`** and **`pytest`** from the repo root. Tests use a temporary SQLite file (see `tests/conftest.py`) so your real `dictionaries/bkc.db` is untouched. GitHub Actions runs **Ruff** plus the test suite on Python 3.11 and 3.12 (`.github/workflows/ci.yml`).

## Community Edition (CE) — auth, tenants, and API

BKC CE targets **trusted homelab or small MSP-style lab** use: sign-in is required for the web UI, inventory is **per-tenant** on disk, and a **read-only JSON API** supports automation.

### First-time sign-in (bootstrap)

1. Set a one-time bootstrap password in the environment, then start the app (or container):

   - `BKC_BOOTSTRAP_ADMIN_PASSWORD` — required to auto-create the first account when the SQLite DB has zero users.
   - `BKC_BOOTSTRAP_ADMIN_USERNAME` — optional, defaults to `admin`.

2. Open the UI, sign in, then **remove** the bootstrap password from the environment so it cannot recreate accounts on a fresh volume by accident.

3. Optional: set `BKC_SECRET_KEY` for stable Flask sessions across restarts (otherwise a file under `keys/bkc_flask_secret` is generated).

### Roles (tenant-scoped)

| Role | Typical use |
|------|----------------|
| `viewer` | Read-only UI. |
| `operator` | Edit inventory, templates, discovery, and read-only integration checks (test connection, pull inventory). |
| `owner` | Operator capabilities plus saving integration credentials, Proxmox clone operations, and **Admin Mode** (SSH commands and Ansible playbooks). |
| Platform **superuser** | Everything owners have, plus **Platform settings** (`/settings`): users, tenants, API keys, audit tail. |

### Data layout

- **Auth / RBAC / audit / API key hashes**: `dictionaries/bkc.db` (gitignored).
- **Inventory overrides per tenant**: `dictionaries/tenants/<slug>/rules.local.json` (gitignored parent). The legacy `dictionaries/rules.local.json` is still read for the `default` tenant until you save from the UI, which writes the tenant path first.
- **Integrations and snapshots per tenant**: `dictionaries/tenants/<slug>/integrations.json`, `proxmox_inventory.json`, and `ansible_scan.json`. For the `default` tenant only, legacy files under `dictionaries/` (`integrations.json`, etc.) are still read if the per-tenant files do not exist yet.
- **Optional per-tenant file templates**: if `dictionaries/tenants/<slug>/file_templates/` exists, it overrides the global `file_templates/` for that tenant.

CLI and scripts can pick the tenant with `BKC_TENANT_SLUG` (defaults to `default`).

### Docker Compose (containers)

`docker compose up --build` starts **BKC**, **bkc-worker**, and **Redis**. The BKC service sets `BKC_RATELIMIT_STORAGE_URI=redis://redis:6379/0` and **`BKC_JOB_QUEUE_URL=redis://redis:6379/2`** so long tasks (Proxmox/Ansible inventory sync, Ansible scan, subnet discovery) run in the **worker container** instead of blocking the web UI. Mount `./dictionaries`, `./keys`, and `./file_templates` on both `bkc` and `bkc-worker`. To disable the queue locally, unset `BKC_JOB_QUEUE_URL` on the app (the worker can stay stopped).

**JSON access logs (optional):** set `BKC_ACCESS_LOG_FORMAT=json` on the BKC container to emit **one JSON object per line** to stderr (request id, path, status, duration, user, tenant). Every response also gets an **`X-Request-ID`** header for correlation.

**Session cookies (HTTPS):** when users only reach BKC over TLS, set **`BKC_SESSION_COOKIE_SECURE=1`** (or **`BKC_TRUSTED_HTTPS=1`**) so the Flask session cookie is marked **Secure**. Optional **`BKC_SESSION_SAMESITE`** = `Lax` (default), `Strict`, or `None` (only honored together with Secure). Baseline response headers (**`X-Content-Type-Options`**, **`X-Frame-Options`**, **`Referrer-Policy`**, **`Permissions-Policy`**) are added on every response; disable with **`BKC_DISABLE_SECURITY_HEADERS=1`** only if something in your stack conflicts. See **`SECURITY.md`** for reporting issues.

**Superuser audit export:** `GET /settings/audit/export?format=json|csv` (optional `limit=`, max 100000) downloads the audit log while signed in as a platform superuser.

### Read-only HTTP API (`/api/v1`)

- `GET /api/v1/health` — liveness, no auth.
- `GET /api/v1/ready` — readiness: SQLite (`bkc.db`), Redis when `BKC_RATELIMIT_STORAGE_URI` is `redis://…`, and a write probe under `dictionaries/`. Returns **503** if any check fails (for load balancers / Kubernetes).
- `GET /api/v1/me` and `GET /api/v1/inventory` — `Authorization: Bearer <api_key>` where the key is created under **Platform settings** (superuser only). The plaintext key is shown **once** when created.
- **Scopes** (comma-separated, stored on the key): `read:me`, `read:inventory`, or `*` for all current and future read endpoints. Keys without access to an endpoint receive **403** JSON `{"error":"insufficient_scope",...}`.
- **Rate limits:** `GET /api/v1/me` and `GET /api/v1/inventory` are limited **per API key** (Flask-Limiter). Optional per-key **requests/minute** is set when creating the key; otherwise use **`BKC_API_KEY_RATE_LIMIT`** (default `120 per minute`). **`GET /api/v1/health`** and **`GET /api/v1/ready`** are not rate-limited by this rule.

**Same readiness JSON** is also exposed at **`GET /ready`** on the app port (no auth), for probes that do not use the `/api/v1` prefix.

### Background jobs (RQ)

When `BKC_JOB_QUEUE_URL` points at Redis (Compose uses database **/2** while rate limits use **/0**), selected integration actions and subnet scans are **enqueued** and handled by **`python bkc_worker.py`** (the `bkc-worker` container). Authenticated users can open **`/jobs/<job_id>`** (linked from the **Jobs** nav entry) for status, result JSON, and failures.

### Production habits

- Prefer the included **Caddy** service (`docker compose --profile tls up --build`) or your own reverse proxy for TLS and security headers; keep BKC on an internal Docker network and only publish the proxy.
- Put **real** session secrets in `BKC_SECRET_KEY` or the generated `keys/bkc_flask_secret` volume backup.
- Multi-tenant **MSP isolation** on shared infrastructure still needs agents, jump hosts, or per-customer networking; CE gives you identity, RBAC, and tenant-scoped **inventory files**, not a full zero-trust edge product.

## Secret Storage

BKC now encrypts stored secrets at rest instead of leaving passwords and token values in plaintext JSON.

- Secret fields such as `password`, `controller_password`, and `token_value` are encrypted before being written to `dictionaries/*.json`
- Encryption uses a per-install salt in `dictionaries/secrets_meta.json`
- The master secret is stored in `keys/bkc_master_key` unless you provide `BKC_MASTER_SECRET`

Recovery depends on these two artifacts:

- `keys/bkc_master_key`
- `dictionaries/secrets_meta.json`

Back them up outside the repo. If the app is damaged but those two files survive, BKC can still decrypt the stored secrets.

To migrate existing plaintext secret values into encrypted form, run:

`python3 bkc_cli.py migrate-secrets`

## Local Runtime Data

The repo now ships with sanitized sample inventory data in `dictionaries/rules.json` and `dictionaries/integrations.sample.json`.

- **Inventory:** prefer `dictionaries/tenants/<slug>/rules.local.json`; legacy `dictionaries/rules.local.json` remains supported for the `default` tenant.
- **Integrations:** prefer `dictionaries/tenants/<slug>/integrations.json`; legacy `dictionaries/integrations.json` is still read for `default` until you save integrations in the UI (which writes the tenant path).
- Git ignores `dictionaries/bkc.db`, `dictionaries/tenants/`, `dictionaries/rules.local.json`, and `dictionaries/integrations.json` so real lab state stays out of the repo.

This keeps the tracked repo safe while still letting the app keep real lab state locally.

## Containerized Test Build

The repository now includes a basic container build for the web UI and an optional lab PXE/DHCP service.

### Start the web UI

Build and run the app (starts **BKC** and its **Redis** dependency for rate limiting):

`docker compose up --build`

The UI will be available at `http://localhost:5000`.

### Optional reverse proxy (Caddy, profile `tls`)

`docker compose --profile tls up --build` starts **Caddy** on port **8080** → BKC (see `docker/caddy/Caddyfile`): gzip/zstd, `X-Frame-Options`, `CSP`, and other baseline headers. Map host **80:80** or **443:443** in `docker-compose.yml` when you are ready to put this on a real hostname; add a `tls` block or ACME email in Caddy when you expose HTTPS.

Set **`BKC_BEHIND_PROXY=1`** on the BKC container when using Caddy (or any reverse proxy) so Flask applies **ProxyFix** and `request.remote_addr` / scheme match the client.

### Optional PXE/DHCP lab service

An additional `pxe-lab` service is available behind the `lab` profile:

`docker compose --profile lab up --build`

This service uses `dnsmasq` to provide:

- DHCP
- TFTP
- basic PXE boot advertisement

Important constraints:

- DHCP is MAC-gated only. Unknown clients are ignored.
- The service uses `network_mode: host`, because DHCP and PXE are broadcast-heavy and awkward in normal bridged Docker networking.
- This is intended for a dedicated lab segment, not a shared network.
- You must edit `docker/pxe/dnsmasq.conf` to match your actual interface and subnet before starting it.
- You must populate `docker/pxe/dhcp.hosts` with explicit MAC-to-IP reservations.
- The included PXE config is only a starting point. Real boot chains often need iPXE, HTTP boot assets, UEFI-specific files, or external TFTP content.

### Files

- `Dockerfile`: BKC application image
- `bkc_worker.py`: RQ worker entrypoint (background jobs)
- `docker-compose.yml`: app plus optional lab services
- `docker/caddy/Caddyfile`: optional reverse proxy (profile `tls`)
- `docker/pxe/Dockerfile`: PXE/DHCP service image
- `docker/pxe/dnsmasq.conf`: DHCP/TFTP/PXE config
- `docker/pxe/dhcp.hosts`: MAC allowlist and reservations
- `docker/pxe/tftpboot/`: boot asset root

## Proxmox Walkthrough

The primary integration should be the Proxmox HTTPS API at `https://192.168.1.9:8006/api2/json`, not SSH to the hypervisor.

Use SSH for:

- initial reachability checks
- manual host inspection
- guest bootstrap after the VM exists

Use the API for:

- version checks
- node and VM inventory
- clone/build operations
- lifecycle state tracking

### Recommended auth

Prefer an API token instead of a password. Export either token credentials or username/password:

```bash
export BKC_PROXMOX_API_URL="https://192.168.1.9:8006/api2/json"
export BKC_PROXMOX_USERNAME="root@pam"
export BKC_PROXMOX_TOKEN_NAME="bkc"
export BKC_PROXMOX_TOKEN_VALUE="REPLACE_ME"
export BKC_PROXMOX_VERIFY_SSL="false"
```

If you must use a password:

```bash
export BKC_PROXMOX_API_URL="https://192.168.1.9:8006/api2/json"
export BKC_PROXMOX_USERNAME="root@pam"
export BKC_PROXMOX_PASSWORD="REPLACE_ME"
export BKC_PROXMOX_VERIFY_SSL="false"
```

`BKC_PROXMOX_VERIFY_SSL=false` is useful for a self-signed lab cert. Set it to `true` once you trust the certificate chain.

### CLI checks

Verify API access:

```bash
python3 bkc_cli.py proxmox-check
```

Dump basic cluster inventory:

```bash
python3 bkc_cli.py proxmox-inventory -o proxmox-inventory.json
```

Clone a VM from a template:

```bash
python3 bkc_cli.py proxmox-clone --node pve01 --source-vmid 9000 --new-vmid 101 --name web-101
```

### Web UI controls

The web UI now exposes integration settings under `/integrations`.

From that screen you can:

- save or update Proxmox API settings
- test Proxmox API connectivity
- save Ansible controller metadata for `192.168.1.10`
- generate the BKC SSH automation keypair inside the container-backed `keys/` volume

The generated public key is shown in the UI so you can install it on the Ansible host or other SSH targets.

### How BKC should map to Proxmox

- Group metadata should hold `provider`, `proxmox_node`, and `template_vmid`
- Node metadata should hold `provider`, `vmid`, `state`, and guest connection info
- A normal flow is `planned -> cloned -> provisioned -> configured -> deployed`

That gives BKC a stable workflow:

1. Discover templates and existing VMs from Proxmox.
2. Record the intended VM in BKC inventory.
3. Clone or create the VM via API.
4. Wait for guest networking and SSH.
5. Hand off to cloud-init, Ansible, or BKC-native deployment logic.

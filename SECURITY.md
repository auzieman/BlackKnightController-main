# Security

## Supported versions

Security fixes are applied to the **default branch** of this repository. Use the latest tagged release or commit for production.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for undisclosed security problems.

1. Email or message the maintainer privately with:
   - A short description of the issue
   - Steps to reproduce (or proof-of-concept) if possible
   - Affected component (web UI, `/api/v1`, worker, etc.)
2. Allow reasonable time for triage and a fix before any public disclosure you plan to make.

## Deployment expectations

BKC CE is designed for **trusted networks** (homelab or internal MSP tooling). Operators should:

- Terminate TLS at a reverse proxy (see Compose **Caddy** profile or your own edge).
- Set **`BKC_SESSION_COOKIE_SECURE=1`** (or **`BKC_TRUSTED_HTTPS=1`**) when the app is only reached over HTTPS.
- Protect **`BKC_SECRET_KEY`**, `keys/bkc_master_key`, and `dictionaries/bkc.db` like any other secrets and backups.

Optional: **`BKC_DISABLE_SECURITY_HEADERS=1`** only if a header conflicts with your environment (prefer fixing the proxy instead).

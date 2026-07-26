# BKC site smoke bot

`tools/site_smoke.py` is a safe GET-only patrol for catching obvious UI regressions after route, template, CSS, or JavaScript changes.

Quick anonymous pass:

```bash
./tools/site_smoke.py --base-url http://swarm1.lab.auzietek.com:5000 --max-pages 25
```

Patrol loop while testing manually:

```bash
./tools/site_smoke.py --base-url http://swarm1.lab.auzietek.com:5000 --max-pages 60 --repeat 10 --interval 30
```

Authenticated pass, when using known BKC web credentials:

```bash
BKC_SMOKE_USERNAME=admin BKC_SMOKE_PASSWORD='...' \
  ./tools/site_smoke.py --base-url http://swarm1.lab.auzietek.com:5000 --max-pages 80
```

The bot checks for:

- HTTP 5xx failures
- unexpected auth failures when logged in
- common Flask/Jinja/Werkzeug/SQLAlchemy error text
- missing core static assets
- safe same-origin links discovered from pages

It intentionally avoids mutating routes such as logout, delete, retry, redeploy, and run actions.

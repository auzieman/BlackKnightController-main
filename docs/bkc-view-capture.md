# BKC view capture artifacts

BlackKnightController views can be treated as documentation evidence. Instead of
manually taking screenshots and hoping they stay current, keep a small manifest
of important UI routes and regenerate PNGs when the lab state is ready.

This is useful for:

- README screenshots
- Auzietek / BlackKnight marketing pages
- video decks and recap posts
- before/after UI reviews
- pipeline run evidence

## Capture command

From the repository root:

```bash
python -m pip install playwright
python -m playwright install chromium
python tools/capture_bkc_views.py
```

By default the tool reads:

```text
docs/bkc-view-captures.json
```

and writes PNG files to:

```text
docs/images/generated/
```

The default manifest points at the current lab BKC service:

```text
http://swarm1.lab.auzietek.com:5000
```

That running lab instance should be treated as the fresh source of truth for
resource graph and pipeline captures. If the edge proxy is the intended path for
a recording, override it:

```bash
python tools/capture_bkc_views.py \
  --base-url http://swarm1.lab.auzietek.com:8084
```

## Authenticated captures

BKC routes usually require a session. Keep credentials out of the manifest and
use environment variables when capture automation needs to log in:

```bash
export BKC_CAPTURE_USERNAME='admin'
export BKC_CAPTURE_PASSWORD='...'
python tools/capture_bkc_views.py
```

The tool opens `/login`, lets the normal browser form handle CSRF, and then
captures the requested views.

## Override the target

```bash
python tools/capture_bkc_views.py \
  --base-url http://swarm1.lab.auzietek.com:5000
```

Capture one view:

```bash
python tools/capture_bkc_views.py --view bkc-beta-resources
```

## Container-friendly pattern

When the workstation browser stack is noisy, run the capture tool from a Python
container with the repository mounted. A Playwright image is even cleaner if it
is available in the local Docker cache.

```bash
docker run --rm \
  -v "$PWD:/work" \
  -w /work \
  mcr.microsoft.com/playwright/python:v1.45.0-jammy \
  python tools/capture_bkc_views.py
```

## Manifest shape

Each view declares a stable output name, route path, optional selector to wait
for, and a short description:

```json
{
  "name": "bkc-beta-resources",
  "path": "/resources",
  "wait_for": "#cy, .resource-cytoscape-canvas, .graph-canvas, main",
  "description": "Living resource graph / infrastructure mind view"
}
```

The selector should point at the part of the page that proves the visual state is
ready. For Cytoscape views, prefer the graph canvas or the panel that owns it.

Views can also describe pre-clicks and cropped captures. This is useful when a
page has a presentation mode, a graph focus mode, or story buttons such as
Topology, Pipeline paths, and Edge ownership.

```json
{
  "name": "bkc-beta-edge-graph",
  "path": "/resources",
  "width": 1920,
  "height": 1400,
  "wait_for": "#beta-cy",
  "capture_selector": ".graph-panel",
  "actions": [
    {
      "kind": "click",
      "selector": "[data-view-mode='edge']",
      "settle_ms": 3000
    }
  ]
}
```

Supported action kinds are `click`, `fill`, and `check`. `capture_selector`
creates a tighter artifact from one region of the page, which is a lightweight
way to get a “zoom to diagram” documentation shot before the UI has a dedicated
full-screen graph button.

## Documentation usage

Generated images can be referenced from Markdown:

```markdown
![BKC resource graph](docs/images/generated/bkc-beta-resources.png)
```

For site content, copy or publish selected generated images into the micro-blog
media folder, then reference them from the Auzietek or BlackKnight pages as
visual proof.

## Future pipeline step

A BKC pipeline can wrap this as:

1. refresh inventory / resource graph data
2. open the target view
3. capture PNG
4. attach output path to the run evidence
5. optionally publish selected images to docs or micro-blog media

That gives us a clean `view -> png -> docs/site` path without manual screenshot
drift.

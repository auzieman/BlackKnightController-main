# AI Cytoscape Layout Experiment

This is a side experiment, not a dependency for the filming pipeline.

The resource graph page has an `AI Layout` button that asks local Ollama on
Server1 for Cytoscape node positions. BKC validates the response before it can
touch saved graph positions:

- the model may only use real Cytoscape node IDs;
- coordinates are clamped to the requested canvas;
- at least three model-proposed anchors are required;
- missing node positions are filled by a deterministic fallback grid;
- saved positions still go through the existing `graph_positions` table.

Current model:

- `qwen2.5-coder:1.5b`

Current route:

- `POST /resources/graph/ai-layout`

This keeps the model in the role we want: suggestive, bounded, and replaceable.

## Edge fanout layout fragment — 2026-08-02

The switch-centered lab view now has a deterministic `eastwest` layout mode for
the `fabric:n2024` expansion pack.

Intent:

```text
                         host:pve1
Internet / IONOS         host:server1
Spectrum / IPFire  ->  fabric:n2024  ->  host:server2
                         ns1
                         swarm1
```

The switch remains the anchor. WAN/ISP, IPFire, and IONOS/public-site context
sit to the left. Hypervisors and immediate children sit to the right. Switch
ports and MAC evidence stay close to the resources they prove.

This is the kind of prompt a local model can critique without owning truth:

```text
You are reviewing a visible Cytoscape graph subset from BlackKnightController.

BKC evidence is the source of truth. Do not invent, delete, rename, merge, or
relink resources. Suggest layout only.

Current story goal:

- place the managed switch as the center anchor;
- put public/WAN and IONOS context to the left;
- put IPFire near the left edge of the switch;
- put hypervisors and their immediate children to the right;
- keep pve1, server1/OpenStack, server2/ESXi, ns1, and swarm1 readable;
- keep switch-port/MAC evidence near the resources it proves;
- fade stale/offline items away from the main east/west path;
- reduce edge crossings and label collisions.

Return only JSON with anchors, clusters, relative placement constraints,
safe-to-move node ids, collapse candidates, expected improvement, and
confidence. Prefer constraints over exact coordinates.
```

The accepted first-pass implementation is deterministic:

- `static/beta/beta.js` adds `layout.mode = "eastwest"`;
- `fabric:n2024` adds `wan:internet`, `wan:spectrum`, `edge:ipfire`,
  `remote:ionos`, and `service:public-sites` as context nodes;
- exact positions live in the expansion pack as a view fragment, not an
  infrastructure fact.

Future model route:

1. export the visible `fabric:n2024` neighborhood and current positions;
2. ask Ollama for critique/constraints;
3. validate returned node ids;
4. preview ghost positions;
5. apply only after operator approval;
6. record the accepted positions as a view fragment.

## Nested ownership envelopes — 2026-08-02

Astra's follow-up clarified that `owns` should behave as a structural layout
relationship, not a normal peer edge. A global layout should not be expected to
solve this shape:

```text
hypervisor -> host -> VM -> stack -> 8 containers
```

The first deterministic slice adds `ownershipGrid` layout mode and a concrete
fixture under `stack:blackknight-openstack`:

```text
vm:openstack-manager-01
  -> stack:blackknight-openstack
      -> container:bkc-web
      -> container:bkc-worker
      -> container:bkc-slow-worker
      -> container:bkc-redis
      -> container:bkc-nginx-route
      -> container:bkc-registry-client
      -> container:bkc-fragments
      -> container:bkc-ssh-tools
```

The grid expands locally beside the selected stack. It must not reposition:

- `fabric:n2024`;
- WAN / IONOS anchors;
- neighboring hypervisors;
- upstream OpenStack host/VM ancestors.

This keeps the mental map intact: expanding detail increases local density
inside the owner envelope instead of relaying out the world.

Future validation fixture:

- one hypervisor → host → VM → stack → 8 container grid;
- two hypervisor branches expanded independently;
- no movement of global east/west anchors during local fanout;
- collapse/restore preserves prior positions.

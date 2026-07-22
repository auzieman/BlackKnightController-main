# 40 VIDEO — OpenStack Local AI And Graph Layout

## Goal

Use the OpenStack lab produced by `10 VIDEO`, `10B VIDEO`, and `30 VIDEO` to
host a replaceable local-AI appliance. Measure its real performance, expose
OpenWebUI through the lab edge, and use the Ollama API to produce reviewable
Cytoscape layout proposals for BKC resource and pipeline graphs.

This lane is non-destructive to the hypervisor. Its VM is cattle and may be
deleted and recreated by the pipeline.

## Deployment boundary

- OpenStack owns the Debian VM, flavor, image, volume, security group, and port.
- Ollama runs natively as a systemd service inside the VM. This keeps inference
  benchmarking separate from container overhead and exposes a normal local API.
- OpenWebUI runs as a pinned container inside the VM and talks to Ollama over
  the VM-local interface.
- BKC reaches Ollama and OpenWebUI through declared service endpoints. Neither
  service receives direct access to BKC credentials or runtime dictionaries.
- The first version is CPU-only. GPU passthrough is a later optional fragment,
  not a hidden prerequisite.

## Proposed stages

1. `preflight-openstack-capacity`
   - Validate Keystone, Nova, Neutron, Glance, compute capacity, free storage,
     tenant networking, and the selected Debian image.
   - Resolve all image, flavor, network, and host IDs before changing state.
2. `realize-local-ai-vm`
   - Idempotently create or replace the declared VM, port, security group, and
     optional volume.
   - Inject only the current BKC SSH public key through cloud-init.
3. `validate-vm-firstboot`
   - Wait for ACTIVE, obtain its address, validate SSH and cloud-init
     completion, then record the VM and address as resource facts.
4. `install-ollama-native`
   - Realize a version-pinned installation script, systemd unit, bind address,
     model storage path, and health check over BKC SSH.
5. `deploy-openwebui-container`
   - Run a version-pinned OpenWebUI image with persistent application storage,
     a VM-local Ollama endpoint, restart policy, and HTTP health validation.
6. `pull-and-prove-model`
   - Pull an explicitly selected model after checking its declared storage
     requirement. Record model name, digest, size, and Ollama version.
7. `benchmark-inference`
   - Run fixed warm-up and measured prompts. Record model-load time, prompt
     tokens/s, generation tokens/s, total latency, CPU, memory, and disk use.
8. `generate-layout-proposal`
   - Send a bounded graph projection plus a versioned layout contract to the
     Ollama API. Store the response as a proposal associated with the graph
     revision; never write live positions from raw model output.
9. `validate-layout-proposal`
   - Require exact known node IDs, finite coordinates, permitted parents,
     complete node coverage, no invented edges, bounded coordinates, and a
     minimum spacing rule. Compare crossings and overlap against the current
     deterministic layout.
10. `publish-and-validate`
    - Expose OpenWebUI through the edge proxy, validate Ollama API and OpenWebUI,
      show the benchmark receipt, and offer the accepted layout as a preview.

## Initial VM profile

Start with a configurable CPU-only profile rather than baking a hardware guess
into the action:

```text
vCPU: 4 (pipeline input)
RAM: 8 GiB (pipeline input)
root disk: 40 GiB
model volume: 80 GiB, optional but preferred
OS: Debian 13 generic cloud image
network: tenant network produced by 30 VIDEO
```

The preflight must refuse a selected model whose declared size does not fit the
available model volume or whose requested memory exceeds the selected flavor.

## Layout request contract

The input contains only graph structure and presentation intent:

```json
{
  "schema": "bkc-cytoscape-layout-request-v1",
  "graph_revision": "sha256:...",
  "view": "resource-topology",
  "nodes": [{"id": "host:r630-openstack-01", "kind": "host", "parent": null}],
  "edges": [{"source": "pipeline:10b", "target": "host:r630-openstack-01", "type": "targets"}],
  "constraints": {"minimum_spacing": 90, "direction": "left-to-right"}
}
```

The model response is limited to node placement and optional grouping advice:

```json
{
  "schema": "bkc-cytoscape-layout-proposal-v1",
  "graph_revision": "sha256:...",
  "positions": [{"id": "host:r630-openstack-01", "x": 640, "y": 220}],
  "explanation": "Keep the physical host above its OpenStack workloads."
}
```

The server-side validator, not the model, decides whether the proposal is safe
to preview. Applying an accepted proposal remains an explicit operator action.

## Reusable fragments

The implementation should promote these independently versioned fragments:

- `openstack.vm.ensure`
- `ssh.cloud-init.wait`
- `ollama.native.ensure`
- `ollama.model.ensure`
- `openwebui.container.ensure`
- `ollama.benchmark.run`
- `cytoscape.layout.request`
- `cytoscape.layout.validate`
- `edge.http-route.ensure`

Each fragment owns focused validation and a content digest. Pipeline 40 owns
their ordering and the final end-to-end receipt.

## Recording gate

Do not promote this lane to `40 VIDEO` until a candidate run proves all of the
following twice without direct repair:

- VM recreation and SSH enrollment
- Ollama and OpenWebUI health after VM reboot
- repeatable model pull or cached-model detection
- benchmark receipt with resource measurements
- rejected malformed layout fixture
- accepted valid layout proposal without mutating saved positions
- edge URL returns HTTP 200

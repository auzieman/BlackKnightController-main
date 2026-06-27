# Rx Demo K3s Registry Preflight

Prepares the image distribution path for the rx-demo k3s demo.

This is the "what happens before Kubernetes can pull the app" lane:

1. Confirm a simple `registry:2` endpoint is reachable.
2. Confirm k3s/containerd trusts the registry.
3. Build and push the rx-demo images with a unique tag.
4. Verify the registry catalog exposes the pushed repositories.

Harbor is the production-style answer. `registry:2` is the small demo answer.


# Demo K3s Add Node

Runnable lane for adding one worker node to an existing k3s cluster.

This is the first act of the demo story:

1. Select the worker name and Fedora 44 source VMID 131.
2. Clone and boot the worker VM in Proxmox.
3. Discover the worker through neighbor/ARP data and take it over with SSH.
4. Prepare the host for k3s.
5. Capture a join token from the existing k3s server.
6. Install the k3s agent on the new node.
7. Verify the node is Ready.
8. Extend telemetry and inventory coverage.

The lane is intentionally separate from the full `k3s-fedora-cluster` pipeline.
For the video, we want to show incremental cluster expansion before deploying
`rx-demo`.

## Reset For Another Session

Use the pipeline `Undeploy` action as the demo reset path. It does not remove
the registry or the base Fedora source. It only resets the worker added by this
lane:

1. Select the worker from the parent run metadata, falling back to the target
   name such as `kube3.lab.auzietek.com`.
2. Cordon, drain, and delete the k3s node.
3. Stop and destroy the cloned Proxmox VM.
4. Verify the node is absent from both k3s and Proxmox.

After reset, the deploy action can clone and rebuild the worker again for a
later recording session.

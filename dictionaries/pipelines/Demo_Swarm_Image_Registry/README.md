# Demo Swarm Image Registry

Planning-mode lane for the shared image registry used by the k3s demo.

The registry runs on Docker Swarm, stores image blobs on the shared NFS-backed
runtime path, and gives k3s a normal pull endpoint. This keeps image serving out
of the k3s cluster being demonstrated.


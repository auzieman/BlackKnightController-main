# Rx Demo K3s Deploy

Deploys rx-demo to k3s using the Kubernetes manifests under
`k8s/overlays/lab`, then validates that the Kubernetes outcome matches the
local Docker Compose outcome:

- API and UI health endpoints respond.
- One synthetic prescription can be looked up, approved, and read back.
- Core workloads are ready.
- OTel collector metrics are exposed for Prometheus/Grafana.


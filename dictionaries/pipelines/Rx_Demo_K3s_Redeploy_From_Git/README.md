# Rx Demo K3s Redeploy From Git

Demo lane for commit-triggered redeployment.

The intended live flow:

1. Make and push the CloudEvents audit change.
2. Trigger BKC with the branch/ref and commit hash.
3. Sync the shared rx-demo source checkout from Git.
4. Build and push commit-tagged images to the swarm registry.
5. Update the k3s application deployments to the commit tag.
6. Capture node/cloud-init provenance through BKC SSH.
7. Generate approve/refill activity through rx-demo.
8. Validate metrics, Grafana health, and Loki audit records.

Demo Loki query:

```logql
{service_name=~"rx/.+"} |= "CloudEvent audit" |= "RX-BKC-CLOUDEVENTS"
```

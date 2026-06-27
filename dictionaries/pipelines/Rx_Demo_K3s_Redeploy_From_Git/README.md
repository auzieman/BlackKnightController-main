# Rx Demo K3s Redeploy From Git

Demo lane for commit-triggered redeployment.

The intended live flow:

1. Make a visible UI change.
2. Commit it.
3. Trigger BKC with the commit hash.
4. Build and push commit-tagged images.
5. Update the Kubernetes image tag.
6. Validate the new UI and service health.


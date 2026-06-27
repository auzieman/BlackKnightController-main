# Rx Demo K3s Undeploy

Removes the rx-demo Kubernetes lab workload after the demo.

This lane deliberately preserves external observability and the image registry.
It removes the app namespace and then validates that the namespace is gone.


# VMware k3s Lab Prepare

Draft composition recipe for reusing the existing k3s deployment model after an
ESXi host is available.

This lane should not duplicate the k3s deployment logic. It should bind VMware
capacity, VM placement, DNS, registry, and lab network variables, then invoke
the existing k3s deployment lane once those values are resolved.

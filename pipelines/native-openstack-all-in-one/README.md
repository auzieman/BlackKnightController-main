# Native OpenStack reusable fragments

`10B VIDEO` composes the scripts in `scripts/` as ordered BKC SSH action
fragments. Each fragment is independently realized on the target, executed,
and validated before the next fragment begins. The pipeline remains the owner
of ordering, run events, gates, and final service validation.

The `fragments` manifest in `pipeline.json` records the SHA-256 digest of every
known-good script. Treat a digest change as an intentional fragment revision:
run its focused validation, then prove the complete 10B pipeline before
updating the recorded digest. Do not casually rewrite a proven fragment while
debugging orchestration around it.

The current composition is:

1. `openstack.readiness` — shared target-side readiness/wait primitives.
2. `openstack.keystone-horizon` — database, message bus, Keystone, Apache, and Horizon.
3. `openstack.glance-placement` — image and resource-placement services.
4. `openstack.nova` — single-node compute control plane and hypervisor service.
5. `openstack.neutron-ovs` — Neutron API and Open vSwitch networking.

This is the first concrete implementation of the repository's fragment model:
normal engineering scripts are reusable templates, while BKC supplies target
selection, transport, parameters, sequencing, evidence, and validation.

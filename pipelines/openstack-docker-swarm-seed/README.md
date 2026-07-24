# 40 VIDEO — OpenStack Docker Swarm Seed

This candidate moves the old swarm idea into the freshly built Server1 OpenStack lab.

It deliberately defaults to the proven Debian generic cloud image and keeps Fedora as
optional metadata until an official cloud-image URL is selected and cached. The shape is
the BKC pattern: template cloud-init, create cattle VMs through OpenStack, SSH into the
new hosts, run ordinary Docker Swarm steps, validate, then record fragments.

Known-good guardrails:

- `enable_replace_swarm_vms` defaults to `false`.
- Fedora import defaults to `false`.
- Success requires `docker node ls` to show the three OpenStack VMs as Ready.

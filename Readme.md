#Black Knight Controller

Black Knight Controller is a web-based interface for managing a Fabric-based deployment system. The name is a nod to the urban legend of an ancient alien craft orbiting the Earth, which some people believe is recording and guiding humanity. 

*However, please note that this project is purely fictional and not based on any factual evidence. And definatly not an indication that 42 is the ultimate answer. Also though this concept may seem alien its just a human and some AI working together :) *

The initial functionality of the Black Knight Controller includes the ability to manage groups and hosts, edit group and host information, and deploy code to specific hosts. The interface is designed with a left navigation menu for easy access to the different sections of the application. The theme is rounded and blue, with a modern look and feel.

In addition to the initial functionality, the Black Knight Controller also includes an "Add Nodes" routine. This allows users to add a list of hosts (IP or hostname) to a specific group. The system will test the credentials and save them, or perform a round of interactive steps to copy a preshared key to the destination.

We hope that the Black Knight Controller will help simplify the deployment process and make it more accessible for users of all levels. Please feel free to use, modify, and contribute to this project as you see fit.

Key feature list.

## View and edit configuration files for fabric deployments
- Add, remove, and edit groups of servers to deploy to
- Add, remove, and edit individual servers within a group
- View and edit templates for fabric deployments
- Deploy to individual servers or groups of servers with fabric
- Ability to add new nodes to an existing group with form validation and handling
- User-friendly UI with a modern and clean theme

We hope that the Black Knight Controller will help simplify the deployment process and make it more accessible for users of all levels. Please feel free to use, modify, and contribute to this project as you see fit.

To ensure you have the right python libraries run your OS's version of this command.

`pip install -r requirements.txt`

## Secret Storage

BKC now encrypts stored secrets at rest instead of leaving passwords and token values in plaintext JSON.

- Secret fields such as `password`, `controller_password`, and `token_value` are encrypted before being written to `dictionaries/*.json`
- Encryption uses a per-install salt in `dictionaries/secrets_meta.json`
- The master secret is stored in `keys/bkc_master_key` unless you provide `BKC_MASTER_SECRET`

Recovery depends on these two artifacts:

- `keys/bkc_master_key`
- `dictionaries/secrets_meta.json`

Back them up outside the repo. If the app is damaged but those two files survive, BKC can still decrypt the stored secrets.

To migrate existing plaintext secret values into encrypted form, run:

`python3 bkc_cli.py migrate-secrets`

## Local Runtime Data

The repo now ships with sanitized sample inventory data in `dictionaries/rules.json` and `dictionaries/integrations.sample.json`.

- Local runtime inventory is stored in `dictionaries/rules.local.json`
- Live integration credentials stay in `dictionaries/integrations.json`
- Both local runtime files are ignored by Git

This keeps the tracked repo safe while still letting the app keep real lab state locally.

## Containerized Test Build

The repository now includes a basic container build for the web UI and an optional lab PXE/DHCP service.

### Start the web UI

Build and run the app:

`docker compose up --build bkc`

The UI will be available at `http://localhost:5000`.

### Optional PXE/DHCP lab service

An additional `pxe-lab` service is available behind the `lab` profile:

`docker compose --profile lab up --build`

This service uses `dnsmasq` to provide:

- DHCP
- TFTP
- basic PXE boot advertisement

Important constraints:

- DHCP is MAC-gated only. Unknown clients are ignored.
- The service uses `network_mode: host`, because DHCP and PXE are broadcast-heavy and awkward in normal bridged Docker networking.
- This is intended for a dedicated lab segment, not a shared network.
- You must edit `docker/pxe/dnsmasq.conf` to match your actual interface and subnet before starting it.
- You must populate `docker/pxe/dhcp.hosts` with explicit MAC-to-IP reservations.
- The included PXE config is only a starting point. Real boot chains often need iPXE, HTTP boot assets, UEFI-specific files, or external TFTP content.

### Files

- `Dockerfile`: BKC application image
- `docker-compose.yml`: app plus optional lab services
- `docker/pxe/Dockerfile`: PXE/DHCP service image
- `docker/pxe/dnsmasq.conf`: DHCP/TFTP/PXE config
- `docker/pxe/dhcp.hosts`: MAC allowlist and reservations
- `docker/pxe/tftpboot/`: boot asset root

## Proxmox Walkthrough

The primary integration should be the Proxmox HTTPS API at `https://192.168.1.9:8006/api2/json`, not SSH to the hypervisor.

Use SSH for:

- initial reachability checks
- manual host inspection
- guest bootstrap after the VM exists

Use the API for:

- version checks
- node and VM inventory
- clone/build operations
- lifecycle state tracking

### Recommended auth

Prefer an API token instead of a password. Export either token credentials or username/password:

```bash
export BKC_PROXMOX_API_URL="https://192.168.1.9:8006/api2/json"
export BKC_PROXMOX_USERNAME="root@pam"
export BKC_PROXMOX_TOKEN_NAME="bkc"
export BKC_PROXMOX_TOKEN_VALUE="REPLACE_ME"
export BKC_PROXMOX_VERIFY_SSL="false"
```

If you must use a password:

```bash
export BKC_PROXMOX_API_URL="https://192.168.1.9:8006/api2/json"
export BKC_PROXMOX_USERNAME="root@pam"
export BKC_PROXMOX_PASSWORD="REPLACE_ME"
export BKC_PROXMOX_VERIFY_SSL="false"
```

`BKC_PROXMOX_VERIFY_SSL=false` is useful for a self-signed lab cert. Set it to `true` once you trust the certificate chain.

### CLI checks

Verify API access:

```bash
python3 bkc_cli.py proxmox-check
```

Dump basic cluster inventory:

```bash
python3 bkc_cli.py proxmox-inventory -o proxmox-inventory.json
```

Clone a VM from a template:

```bash
python3 bkc_cli.py proxmox-clone --node pve01 --source-vmid 9000 --new-vmid 101 --name web-101
```

### Web UI controls

The web UI now exposes integration settings under `/integrations`.

From that screen you can:

- save or update Proxmox API settings
- test Proxmox API connectivity
- save Ansible controller metadata for `192.168.1.10`
- generate the BKC SSH automation keypair inside the container-backed `keys/` volume

The generated public key is shown in the UI so you can install it on the Ansible host or other SSH targets.

### How BKC should map to Proxmox

- Group metadata should hold `provider`, `proxmox_node`, and `template_vmid`
- Node metadata should hold `provider`, `vmid`, `state`, and guest connection info
- A normal flow is `planned -> cloned -> provisioned -> configured -> deployed`

That gives BKC a stable workflow:

1. Discover templates and existing VMs from Proxmox.
2. Record the intended VM in BKC inventory.
3. Clone or create the VM via API.
4. Wait for guest networking and SSH.
5. Hand off to cloud-init, Ansible, or BKC-native deployment logic.

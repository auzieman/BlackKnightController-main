from pathlib import Path

import paramiko

from services.integration_store import load_integrations
from services.ssh_keys import read_key_pair


class AnsibleScanError(RuntimeError):
    pass


def _run_command(client: paramiko.SSHClient, command: str) -> str:
    stdin, stdout, stderr = client.exec_command(command)
    output = stdout.read().decode("utf-8", errors="replace")
    error = stderr.read().decode("utf-8", errors="replace")
    if error and not output:
        raise AnsibleScanError(error.strip())
    return output.strip()


def scan_ansible_controller() -> dict:
    integrations = load_integrations()
    ansible = integrations["ansible"]
    ssh = integrations["ssh"]

    controller_host = ansible.get("controller_host", "").strip()
    controller_user = ansible.get("controller_user", "").strip()
    controller_password = ansible.get("controller_password", "").strip()
    inventory_path = ansible.get("inventory_path", "").strip() or "/etc/ansible/hosts"
    config_root = ansible.get("config_root", "").strip() or "/etc/ansible"

    if not controller_host or not controller_user:
        raise AnsibleScanError("Set Ansible controller host and user first.")

    key_info = read_key_pair(ssh["private_key_path"], ssh["public_key_path"])
    private_key = Path(key_info["private_key_path"])

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        connect_kwargs = {
            "hostname": controller_host,
            "username": controller_user,
            "timeout": 10,
        }
        if controller_password:
            connect_kwargs["password"] = controller_password
        elif private_key.exists():
            connect_kwargs["key_filename"] = str(private_key)
        else:
            raise AnsibleScanError(
                "No usable auth for Ansible controller. Install the BKC SSH key or provide a controller password."
            )

        client.connect(**connect_kwargs)
        inventory_content = _run_command(client, f"cat {inventory_path}")
        playbooks_raw = _run_command(
            client,
            f"find {config_root} -maxdepth 3 -type f \\( -name '*.yml' -o -name '*.yaml' \\) | sort",
        )
    except Exception as exc:
        raise AnsibleScanError(str(exc)) from exc
    finally:
        client.close()

    playbooks = [line for line in playbooks_raw.splitlines() if line.strip()]
    return {
        "controller_host": controller_host,
        "controller_user": controller_user,
        "inventory_path": inventory_path,
        "config_root": config_root,
        "auth_method": "password" if controller_password else "ssh-key",
        "inventory_content": inventory_content,
        "playbooks": playbooks,
    }

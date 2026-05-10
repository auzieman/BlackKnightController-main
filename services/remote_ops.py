from __future__ import annotations

from pathlib import Path

import paramiko

from services.integration_store import load_integrations
from services.ssh_keys import read_key_pair


class RemoteCommandError(RuntimeError):
    pass


def run_remote_command(
    *,
    host: str,
    user: str,
    command: str,
    password: str = "",
    timeout: int = 30,
) -> str:
    if not host or not user:
        raise RemoteCommandError("Remote host and user are required.")

    integrations = load_integrations()
    ssh = integrations["ssh"]
    key_info = read_key_pair(ssh["private_key_path"], ssh["public_key_path"])
    private_key = Path(key_info["private_key_path"])

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        connect_kwargs = {
            "hostname": host,
            "username": user,
            "timeout": timeout,
        }
        if password:
            connect_kwargs["password"] = password
        elif private_key.exists():
            connect_kwargs["key_filename"] = str(private_key)
        else:
            raise RemoteCommandError(
                "No usable SSH auth. Install the BKC SSH key or provide a password."
            )

        client.connect(**connect_kwargs)
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_status = stdout.channel.recv_exit_status()
        output = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise RemoteCommandError(str(exc)) from exc
    finally:
        client.close()

    if exit_status != 0:
        detail = error.strip() or output.strip() or f"remote exit status {exit_status}"
        raise RemoteCommandError(detail)

    return output.strip()

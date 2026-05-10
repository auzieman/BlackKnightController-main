import os
import time
from urllib.parse import urlparse

from services.integration_store import load_integrations

try:
    from proxmoxer import ProxmoxAPI
except ImportError:  # pragma: no cover - depends on runtime env
    ProxmoxAPI = None


class ProxmoxConfigError(RuntimeError):
    pass


class ProxmoxAPIError(RuntimeError):
    pass


def load_proxmox_config() -> dict:
    stored = load_integrations()["proxmox"]
    api_url = os.getenv("BKC_PROXMOX_API_URL", stored.get("api_url", "")).rstrip("/")
    token_name = os.getenv("BKC_PROXMOX_TOKEN_NAME", stored.get("token_name", ""))
    token_value = os.getenv("BKC_PROXMOX_TOKEN_VALUE", stored.get("token_value", ""))
    username = os.getenv("BKC_PROXMOX_USERNAME", stored.get("username", ""))
    password = os.getenv("BKC_PROXMOX_PASSWORD", stored.get("password", ""))
    verify_ssl = os.getenv(
        "BKC_PROXMOX_VERIFY_SSL",
        str(stored.get("verify_ssl", False)).lower(),
    ).lower() in {"1", "true", "yes"}

    if not api_url:
        raise ProxmoxConfigError("BKC_PROXMOX_API_URL is not set.")

    parsed = urlparse(api_url if "://" in api_url else f"https://{api_url}")
    host = parsed.hostname
    port = parsed.port or 8006
    if not host:
        raise ProxmoxConfigError(f"Invalid Proxmox API URL: {api_url}")

    if "!" in token_name:
        token_name = token_name.split("!", 1)[1]

    using_token = bool(token_name and token_value)
    using_password = bool(username and password)
    if not using_token and not using_password:
        raise ProxmoxConfigError(
            "Set either token auth or username/password auth via BKC_PROXMOX_* environment variables."
        )

    return {
        "api_url": api_url,
        "host": host,
        "port": port,
        "token_name": token_name,
        "token_value": token_value,
        "username": username,
        "password": password,
        "verify_ssl": verify_ssl,
        "using_token": using_token,
    }


class ProxmoxClient:
    def __init__(self, config: dict):
        if ProxmoxAPI is None:
            raise ProxmoxConfigError(
                "proxmoxer is not installed. Add it to the environment before using Proxmox features."
            )

        self.host = config["host"]
        self.port = config["port"]
        self.username = config["username"]
        self.password = config["password"]
        self.token_name = config["token_name"]
        self.token_value = config["token_value"]
        self.verify_ssl = config["verify_ssl"]
        self.using_token = config["using_token"]
        self.request_trace = []

        kwargs = {
            "host": self.host,
            "port": self.port,
            "verify_ssl": self.verify_ssl,
            "service": "PVE",
        }
        if self.using_token:
            kwargs.update(
                {
                    "user": self.username,
                    "token_name": self.token_name,
                    "token_value": self.token_value,
                }
            )
        else:
            kwargs.update({"user": self.username, "password": self.password})

        try:
            self.client = ProxmoxAPI(**kwargs)
        except Exception as exc:  # pragma: no cover - runtime dependent
            raise ProxmoxAPIError(str(exc)) from exc

    def _trace(self, method: str, path: str) -> None:
        self.request_trace.append(
            {
                "method": method,
                "path": path,
                "url": f"https://{self.host}:{self.port}/api2/json{path}",
            }
        )

    def version(self) -> dict:
        path = "/version"
        self._trace("GET", path)
        try:
            return self.client.version.get()
        except Exception as exc:
            raise ProxmoxAPIError(f"GET {path} failed: {exc}") from exc

    def nodes(self) -> list[dict]:
        path = "/nodes"
        self._trace("GET", path)
        try:
            return self.client.nodes.get()
        except Exception as exc:
            raise ProxmoxAPIError(f"GET {path} failed: {exc}") from exc

    def list_qemu(self, node: str) -> list[dict]:
        path = f"/nodes/{node}/qemu"
        self._trace("GET", path)
        try:
            return self.client.nodes(node).qemu.get()
        except Exception as exc:
            raise ProxmoxAPIError(f"GET {path} failed: {exc}") from exc

    def list_lxc(self, node: str) -> list[dict]:
        path = f"/nodes/{node}/lxc"
        self._trace("GET", path)
        try:
            return self.client.nodes(node).lxc.get()
        except Exception as exc:
            raise ProxmoxAPIError(f"GET {path} failed: {exc}") from exc

    def list_storage(self, node: str) -> list[dict]:
        path = f"/nodes/{node}/storage"
        self._trace("GET", path)
        try:
            return self.client.nodes(node).storage.get()
        except Exception as exc:
            raise ProxmoxAPIError(f"GET {path} failed: {exc}") from exc

    def list_storage_content(self, node: str, storage: str) -> list[dict]:
        path = f"/nodes/{node}/storage/{storage}/content"
        self._trace("GET", path)
        try:
            return self.client.nodes(node).storage(storage).content.get()
        except Exception as exc:
            raise ProxmoxAPIError(f"GET {path} failed: {exc}") from exc

    def next_vmid(self) -> int:
        path = "/cluster/nextid"
        self._trace("GET", path)
        try:
            return int(self.client.cluster.nextid.get())
        except Exception as exc:
            raise ProxmoxAPIError(f"GET {path} failed: {exc}") from exc

    def clone_vm(self, node: str, source_vmid: int, new_vmid: int, name: str, full: bool = True) -> dict:
        path = f"/nodes/{node}/qemu/{source_vmid}/clone"
        self._trace("POST", path)
        try:
            return self.client.nodes(node).qemu(source_vmid).clone.post(
                newid=str(new_vmid),
                name=name,
                full=1 if full else 0,
            )
        except Exception as exc:
            raise ProxmoxAPIError(f"POST {path} failed: {exc}") from exc

    def clone_lxc(self, node: str, source_vmid: int, new_vmid: int, hostname: str, full: bool = True) -> dict:
        path = f"/nodes/{node}/lxc/{source_vmid}/clone"
        self._trace("POST", path)
        try:
            return self.client.nodes(node).lxc(source_vmid).clone.post(
                newid=str(new_vmid),
                hostname=hostname,
                full=1 if full else 0,
            )
        except Exception as exc:
            raise ProxmoxAPIError(f"POST {path} failed: {exc}") from exc

    def task_status(self, node: str, upid: str) -> dict:
        path = f"/nodes/{node}/tasks/{upid}/status"
        self._trace("GET", path)
        try:
            return self.client.nodes(node).tasks(upid).status.get()
        except Exception as exc:
            raise ProxmoxAPIError(f"GET {path} failed: {exc}") from exc

    def wait_for_task(self, node: str, upid: str, timeout: int = 300, poll_interval: int = 3) -> dict:
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            last = self.task_status(node, upid)
            status = str(last.get("status", "")).strip().lower()
            if status != "running":
                return last
            time.sleep(poll_interval)
        raise ProxmoxAPIError(f"Task {upid} on {node} did not complete within {timeout} seconds.")

    def start_vm(self, node: str, vmid: int) -> dict:
        path = f"/nodes/{node}/qemu/{vmid}/status/start"
        self._trace("POST", path)
        try:
            return self.client.nodes(node).qemu(vmid).status.start.post()
        except Exception as exc:
            raise ProxmoxAPIError(f"POST {path} failed: {exc}") from exc

    def vm_status(self, node: str, vmid: int) -> dict:
        path = f"/nodes/{node}/qemu/{vmid}/status/current"
        self._trace("GET", path)
        try:
            return self.client.nodes(node).qemu(vmid).status.current.get()
        except Exception as exc:
            raise ProxmoxAPIError(f"GET {path} failed: {exc}") from exc

    def wait_for_vm_status(self, node: str, vmid: int, expected: str, timeout: int = 120, poll_interval: int = 3) -> dict:
        deadline = time.time() + timeout
        expected_norm = (expected or "").strip().lower()
        last = {}
        while time.time() < deadline:
            last = self.vm_status(node, vmid)
            status = str(last.get("status", "")).strip().lower()
            if status == expected_norm:
                return last
            time.sleep(poll_interval)
        raise ProxmoxAPIError(
            f"VM {vmid} on {node} did not reach status {expected!r} within {timeout} seconds."
        )


def summarize_inventory(client: ProxmoxClient) -> dict:
    nodes = client.nodes()
    qemu_vms = []
    lxc_vms = []
    for node in nodes:
        node_name = node.get("node")
        if not node_name:
            continue
        for vm in client.list_qemu(node_name):
            vm["node"] = vm.get("node", node_name)
            vm["type"] = vm.get("type", "qemu")
            qemu_vms.append(vm)
        for vm in client.list_lxc(node_name):
            vm["node"] = vm.get("node", node_name)
            vm["type"] = vm.get("type", "lxc")
            lxc_vms.append(vm)
    return {
        "version": client.version(),
        "nodes": nodes,
        "virtual_machines": qemu_vms,
        "containers": lxc_vms,
        "request_trace": client.request_trace,
    }


def sync_inventory_to_rules(rules: dict, inventory: dict) -> dict:
    created_groups = 0
    created_nodes = 0
    updated_nodes = 0

    all_vms = list(inventory.get("virtual_machines", [])) + list(inventory.get("containers", []))
    for vm in all_vms:
        node_name = vm.get("node") or "unknown"
        group_name = f"proxmox-{node_name}"
        group_exists = group_name in rules["groups"]
        group = rules["groups"].setdefault(group_name, {"locals": {}, "nodes": {}})
        if not group_exists:
            created_groups += 1
        group["locals"].update(
            {
                "env": group["locals"].get("env", "lab"),
                "datacenter": group["locals"].get("datacenter", "homelab"),
                "release": group["locals"].get("release", "discovered"),
                "provider": "proxmox",
                "proxmox_node": node_name,
                "workflow": group["locals"].get(
                    "workflow", "build -> provision -> configure -> deploy"
                ),
            }
        )

        host_key = vm.get("name") or f"{vm.get('type', 'vm')}-{vm.get('vmid', 'unknown')}"
        existing = group["nodes"].get(host_key, {})
        if existing:
            updated_nodes += 1
        else:
            created_nodes += 1
        group["nodes"][host_key] = {
            **existing,
            "provider": "proxmox",
            "proxmox_node": node_name,
            "resource_type": vm.get("type", "qemu"),
            "vmid": vm.get("vmid", ""),
            "state": vm.get("status", existing.get("state", "unknown")),
            "application": existing.get("application", "discovered"),
            "configuration": existing.get("configuration", "ansible"),
            "provisioner": existing.get("provisioner", "cloud-init"),
            "user": existing.get("user", "root"),
            "port": existing.get("port", 22),
            "private_key": existing.get("private_key", ""),
            "ip": existing.get("ip", ""),
        }

    return {
        "groups": created_groups,
        "created_nodes": created_nodes,
        "updated_nodes": updated_nodes,
    }


def build_catalog(client: ProxmoxClient) -> dict:
    nodes = client.nodes()
    templates = []
    virtual_machines = []
    containers = []
    storage_map = []
    iso_images = []
    container_templates = []

    for node in nodes:
        node_name = node.get("node")
        if not node_name:
            continue

        qemu_items = client.list_qemu(node_name)
        lxc_items = client.list_lxc(node_name)
        storages = client.list_storage(node_name)

        for vm in qemu_items:
            entry = {**vm, "node": vm.get("node", node_name), "type": "qemu"}
            virtual_machines.append(entry)
            if str(vm.get("template", 0)) == "1":
                templates.append(entry)

        for container in lxc_items:
            containers.append({**container, "node": container.get("node", node_name), "type": "lxc"})

        for storage in storages:
            storage_entry = {**storage, "node": node_name}
            storage_map.append(storage_entry)
            storage_name = storage.get("storage")
            if not storage_name or storage.get("active") in {0, "0", False}:
                continue
            try:
                for content in client.list_storage_content(node_name, storage_name):
                    content_type = content.get("content")
                    content_entry = {**content, "node": node_name, "storage": storage_name}
                    if content_type == "iso":
                        iso_images.append(content_entry)
                    elif content_type == "vztmpl":
                        container_templates.append(content_entry)
            except ProxmoxAPIError:
                continue

    next_vmid = None
    try:
        next_vmid = client.next_vmid()
    except ProxmoxAPIError:
        next_vmid = None

    return {
        "nodes": nodes,
        "virtual_machines": virtual_machines,
        "templates": templates,
        "containers": containers,
        "storage": storage_map,
        "iso_images": iso_images,
        "container_templates": container_templates,
        "next_vmid": next_vmid,
        "request_trace": client.request_trace,
    }

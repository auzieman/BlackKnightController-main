from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from services.integration_store import load_integrations


class KubernetesScanError(RuntimeError):
    pass


def _expand_path(value: str) -> str:
    return str(Path(value).expanduser())


def _kubectl_json(args: list[str], kubeconfig: str, context: str) -> dict:
    cmd = ["kubectl", "--kubeconfig", kubeconfig]
    if context:
        cmd.extend(["--context", context])
    cmd.extend(args)
    env = {**os.environ, "KUBECONFIG": kubeconfig}
    try:
        completed = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            env=env,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise KubernetesScanError("kubectl is not installed on the BKC host.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise KubernetesScanError(detail) from exc
    except subprocess.TimeoutExpired as exc:
        raise KubernetesScanError("kubectl timed out while contacting the cluster.") from exc

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise KubernetesScanError("kubectl returned non-JSON output.") from exc


def scan_kubernetes_cluster() -> dict:
    integrations = load_integrations()
    cfg = integrations["kubernetes"]
    kubeconfig = _expand_path(str(cfg.get("kubeconfig_path") or "~/.kube/config"))
    context = str(cfg.get("context") or "").strip()
    namespace = str(cfg.get("namespace") or "default").strip()

    if not Path(kubeconfig).exists():
        raise KubernetesScanError(f"Kubeconfig not found: {kubeconfig}")

    nodes = _kubectl_json(["get", "nodes", "-o", "json"], kubeconfig, context)
    namespaces = _kubectl_json(["get", "namespaces", "-o", "json"], kubeconfig, context)
    pods = _kubectl_json(["get", "pods", "-A", "-o", "json"], kubeconfig, context)
    services = _kubectl_json(["get", "services", "-A", "-o", "json"], kubeconfig, context)

    node_items = nodes.get("items", [])
    pod_items = pods.get("items", [])
    service_items = services.get("items", [])

    return {
        "cluster_name": str(cfg.get("cluster_name") or context or "kubernetes"),
        "api_url": str(cfg.get("api_url") or ""),
        "context": context,
        "namespace": namespace,
        "kubeconfig_path": kubeconfig,
        "nodes": [_summarize_node(item) for item in node_items],
        "namespaces": [
            str(item.get("metadata", {}).get("name", ""))
            for item in namespaces.get("items", [])
            if item.get("metadata", {}).get("name")
        ],
        "pods": [_summarize_pod(item) for item in pod_items],
        "services": [_summarize_service(item) for item in service_items],
    }


def _summarize_node(item: dict) -> dict:
    metadata = item.get("metadata", {})
    status = item.get("status", {})
    info = status.get("nodeInfo", {})
    addresses = {
        str(addr.get("type")): str(addr.get("address"))
        for addr in status.get("addresses", [])
        if addr.get("type") and addr.get("address")
    }
    conditions = {
        str(cond.get("type")): str(cond.get("status"))
        for cond in status.get("conditions", [])
        if cond.get("type")
    }
    labels = metadata.get("labels", {})
    role = "worker"
    if "node-role.kubernetes.io/control-plane" in labels or "node-role.kubernetes.io/master" in labels:
        role = "control-plane"
    return {
        "name": str(metadata.get("name", "")),
        "role": role,
        "ready": conditions.get("Ready", "Unknown"),
        "internal_ip": addresses.get("InternalIP", ""),
        "os_image": str(info.get("osImage", "")),
        "kernel": str(info.get("kernelVersion", "")),
        "kubelet": str(info.get("kubeletVersion", "")),
        "container_runtime": str(info.get("containerRuntimeVersion", "")),
    }


def _summarize_pod(item: dict) -> dict:
    metadata = item.get("metadata", {})
    status = item.get("status", {})
    return {
        "namespace": str(metadata.get("namespace", "")),
        "name": str(metadata.get("name", "")),
        "phase": str(status.get("phase", "")),
        "node": str(status.get("hostIP", "")),
        "pod_ip": str(status.get("podIP", "")),
    }


def _summarize_service(item: dict) -> dict:
    metadata = item.get("metadata", {})
    spec = item.get("spec", {})
    ports = []
    for port in spec.get("ports", []):
        ports.append(
            {
                "name": str(port.get("name", "")),
                "port": port.get("port"),
                "target_port": port.get("targetPort"),
                "node_port": port.get("nodePort"),
                "protocol": str(port.get("protocol", "")),
            }
        )
    return {
        "namespace": str(metadata.get("namespace", "")),
        "name": str(metadata.get("name", "")),
        "type": str(spec.get("type", "")),
        "cluster_ip": str(spec.get("clusterIP", "")),
        "ports": ports,
    }

from __future__ import annotations

from services import resource_graph
from services.action_catalog import action_by_id, actions_for_kind, list_actions
from services.pipeline_catalog import pipeline_by_id
from services.pipeline_executor import workflow_stage_definitions


def test_action_catalog_has_unique_ids():
    actions = list_actions()
    ids = [action["id"] for action in actions]
    assert len(ids) == len(set(ids))
    assert action_by_id("ssh.nfs.ensure_mounts")["risk"] == "medium"
    assert action_by_id("does.not.exist") is None


def test_actions_can_be_selected_by_resource_kind():
    node_actions = {action["id"] for action in actions_for_kind("kubernetes-node")}
    assert "ssh.probe" in node_actions
    assert "ssh.nfs.ensure_mounts" in node_actions
    assert "k3s.nodes.ready" in node_actions


def test_k3s_housekeeping_pipeline_declares_catalog_actions():
    pipeline = pipeline_by_id("k3s-host-telemetry")
    assert pipeline is not None
    action_ids = set(pipeline.get("actions", []))
    assert "ssh.nfs.ensure_mounts" in action_ids
    assert "k3s.manifest.apply" in action_ids
    assert "prometheus.targets.verify" in action_ids


def test_k3s_housekeeping_stage_plan_is_action_annotated():
    stages = workflow_stage_definitions("k3s-host-telemetry")
    actions_by_stage = {stage["name"]: stage.get("action") for stage in stages}
    assert actions_by_stage["verify-k3s"] == "k3s.nodes.ready"
    assert actions_by_stage["nfs-projects"] == "ssh.nfs.ensure_mounts"
    assert actions_by_stage["scrape-validate"] == "prometheus.targets.verify"


def test_auzix_vm130_pipeline_has_repeatable_deploy_contract():
    pipeline = pipeline_by_id("auzix-vm130-deploy")
    assert pipeline is not None
    assert pipeline["repo"] == "AuziX"
    assert pipeline["stages"] == ["source-verify", "runtime-deploy", "network-validate"]

    stages = workflow_stage_definitions("auzix-vm130-deploy")
    kinds = {stage["name"]: stage.get("kind", "remote-command") for stage in stages}
    assert kinds["runtime-deploy"] == "auzix-vm130-deploy"
    assert kinds["network-validate"] == "auzix-vm130-validate"

    source_verify = next(stage for stage in stages if stage["name"] == "source-verify")
    assert "libnssckbi.so" in source_verify["command"]
    assert "mdev.conf" in source_verify["command"]


def test_resource_graph_includes_action_catalog_resources(monkeypatch):
    monkeypatch.setattr(resource_graph, "load_rules", lambda: {"globals": {}, "groups": {}})
    monkeypatch.setattr(
        resource_graph,
        "load_integrations",
        lambda: {"ssh": {}, "proxmox": {}, "ansible": {}, "docker": {}},
    )
    monkeypatch.setattr(resource_graph, "load_proxmox_snapshot", lambda: {})
    monkeypatch.setattr(resource_graph, "load_docker_snapshot", lambda: {})
    monkeypatch.setattr(resource_graph, "load_ansible_snapshot", lambda: {})
    monkeypatch.setattr(resource_graph, "load_runs", lambda: [])

    graph = resource_graph.build_resource_graph()
    resources_by_id = graph["resources_by_id"]
    assert "action:ssh.nfs.ensure_mounts" in resources_by_id
    assert resources_by_id["action:ssh.nfs.ensure_mounts"]["kind"] == "action"
    assert any(
        relationship["source_id"] == "pipeline:k3s-host-telemetry"
        and relationship["type"] == "composes"
        and relationship["target_id"] == "action:ssh.nfs.ensure_mounts"
        for relationship in graph["relationships"]
    )

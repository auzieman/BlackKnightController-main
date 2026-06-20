from __future__ import annotations

from services import pipeline_catalog, resource_graph
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
    assert pipeline["source_path"].endswith(
        "dictionaries/pipelines/AuziX_VM130_Deploy/pipeline.json"
    )
    assert "target-disk-size" in pipeline["gates"]
    assert {item["id"] for item in pipeline["items"]} >= {
        "source-commit",
        "target-disk-size",
        "installed-root-finalizer",
        "network-browser-validation",
    }

    stages = workflow_stage_definitions("auzix-vm130-deploy")
    kinds = {stage["name"]: stage.get("kind", "remote-command") for stage in stages}
    assert kinds["runtime-deploy"] == "auzix-vm130-deploy"
    assert kinds["network-validate"] == "auzix-vm130-validate"

    source_verify = next(stage for stage in stages if stage["name"] == "source-verify")
    assert "libnssckbi.so" in source_verify["command"]
    assert "mdev.conf" in source_verify["command"]


def test_auzix_vm134_install_refresh_has_guarded_install_contract():
    pipeline = pipeline_by_id("auzix-vm134-install-refresh")
    assert pipeline is not None
    assert pipeline["repo"] == "AuziX"
    assert pipeline["resource_class"] == "slow"
    assert pipeline["stages"] == [
        "source-verify",
        "installer-root-build",
        "iso-build",
        "iso-publish",
        "vm-target-verify",
        "install-handoff",
    ]
    assert pipeline["source_path"].endswith(
        "dictionaries/pipelines/AuziX_VM134_Install_Refresh/pipeline.json"
    )
    assert "vm134-target-disk" in pipeline["gates"]
    assert {item["id"] for item in pipeline["items"]} >= {
        "source-commit",
        "installer-runtime",
        "grub-runtime",
        "vm134-target-disk",
        "vm134-boot-media",
    }

    stages = workflow_stage_definitions("auzix-vm134-install-refresh")
    commands = "\n".join(str(stage.get("command", "")) for stage in stages)
    assert "apt-get update" in commands
    assert "grub2-common grub-pc-bin" in commands
    assert "auzix-strict-live-tools" in commands
    assert "auzix-strict-installer-test" in commands
    assert "auzix-strict-grub" in commands
    assert "auzix-strict-audit" not in commands
    assert "auzix-strict-desktop-vm134.iso" in commands
    assert "qm set 134 --ide2" in commands
    assert "--force --bootloader grub" not in commands


def test_lab_demo_rebuilds_missing_tabor_builder_image():
    stages = workflow_stage_definitions("lab-demo")
    builder_ready = next(stage for stage in stages if stage["name"] == "builder-ready")
    command = builder_ready["command"]

    assert "docker image inspect tabor-linux-forge-kernel" in command
    assert "docker compose -f /srv/stacks/tabor-linux-forge/docker-compose.yml build kernel-builder" in command


def test_folder_backed_pipeline_loader_keeps_items_scoped(monkeypatch, tmp_path):
    pipeline_dir = tmp_path / "dictionaries" / "pipelines" / "Example_Pipeline"
    items_dir = pipeline_dir / "items"
    items_dir.mkdir(parents=True)
    (pipeline_dir / "pipeline.json").write_text(
        """{
  "id": "example-folder-pipeline",
  "name": "Example Folder Pipeline",
  "repo": "Example",
  "workflow": "candidate-import",
  "description": "Loaded from dictionaries/pipelines.",
  "stages": ["preflight"],
  "editable": true
}
""",
        encoding="utf-8",
    )
    (items_dir / "00-preflight.json").write_text(
        """{
  "kind": "gate",
  "summary": "Preflight gate"
}
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(pipeline_catalog, "BASE_DIR", tmp_path)

    pipeline = pipeline_catalog.pipeline_by_id("example-folder-pipeline")
    assert pipeline is not None
    assert pipeline["source_path"].endswith("Example_Pipeline/pipeline.json")
    assert pipeline["items"][0]["id"] == "00-preflight"
    assert pipeline["items"][0]["source_path"].endswith("items/00-preflight.json")


def test_auzix_installer_pipeline_is_non_destructive():
    pipeline = pipeline_by_id("auzix-installer-foundation")
    assert pipeline is not None
    assert pipeline["repo"] == "AuziX"
    assert pipeline["resource_class"] == "slow"
    assert pipeline["stages"] == ["source-verify", "installer-build", "contract-test", "artifact-report"]

    stages = workflow_stage_definitions("auzix-installer-foundation")
    commands = "\n".join(str(stage.get("command", "")) for stage in stages)
    assert "build-auzix-installer-package.sh" in commands
    assert "test-auzix-installer" in commands
    assert "auzix-install-disk" not in commands
    assert "192.168.1.163" not in commands


def test_cluster_storage_pipeline_is_idempotent_and_retains_reserve():
    pipeline = pipeline_by_id("lab-cluster-storage")
    assert pipeline is not None
    assert pipeline["actions"] == ["ssh.lvm.grow_root"]

    stages = workflow_stage_definitions("lab-cluster-storage")
    assert [stage["name"] for stage in stages] == [
        "storage-preflight",
        "swarm-grow",
        "k3s-grow",
        "storage-verify",
    ]
    commands = "\n".join(str(stage.get("command", "")) for stage in stages)
    assert "lvextend -r -L 50G" in commands
    assert 'findmnt -bn -o SIZE /' in commands
    assert "swarm3.lab.auzietek.com" in commands
    assert "192.168.1.59" in commands


def test_installer_package_bot_runs_on_slow_queue_with_guarded_runner():
    pipeline = pipeline_by_id("auzix-installer-package-bot")
    assert pipeline is not None
    assert pipeline["resource_class"] == "slow"
    assert pipeline["stages"] == [
        "source-verify",
        "queue-contract",
        "package-build",
        "artifact-report",
        "repository-build",
        "repository-publish",
        "repository-verify",
    ]

    stages = workflow_stage_definitions("auzix-installer-package-bot")
    commands = "\n".join(str(stage.get("command", "")) for stage in stages)
    assert "run-auzix-package-bot.sh" in commands
    assert "installer-ui.queue.json" in commands
    assert "installer-ui.sources.json" in commands
    assert "auzix/builder:local" in commands
    assert "docker image inspect auzix/builder:local" in commands
    assert "docker build --pull=false" in commands
    assert "apt-get update" in commands
    assert "xinit xserver-xorg-legacy" in commands
    assert "build-auzix-package-repo.sh" in commands
    assert "publish-auzix-package-repo.sh" in commands
    assert "http://192.168.1.10/auzix/repo/index.json" in commands
    assert "git commit" not in commands
    assert "git push" not in commands


def test_trixie_package_intake_is_bounded_and_failure_tolerant():
    pipeline = pipeline_by_id("auzix-trixie-package-intake")
    assert pipeline is not None
    assert pipeline["resource_class"] == "slow"
    assert pipeline["stages"] == [
        "source-verify",
        "builder-prepare",
        "package-intake",
        "repository-build",
        "repository-publish",
        "repository-verify",
    ]

    stages = workflow_stage_definitions("auzix-trixie-package-intake")
    commands = "\n".join(str(stage.get("command", "")) for stage in stages)
    assert "docker/trixie-builder/Dockerfile" in commands
    assert "run-auzix-trixie-intake.sh" in commands
    assert "profiles/packages/auzix-trixie-user-apps.packages" in commands
    assert "publish-auzix-package-repo.sh" in commands
    assert "git commit" not in commands
    assert "git push" not in commands


def test_office_package_smoke_builds_tests_and_publishes_two_packages():
    pipeline = pipeline_by_id("auzix-office-package-smoke")
    assert pipeline is not None
    assert pipeline["resource_class"] == "slow"
    assert pipeline["stages"] == [
        "source-verify",
        "builder-prepare",
        "package-build",
        "package-test",
        "repository-build",
        "repository-publish",
        "repository-verify",
    ]

    stages = workflow_stage_definitions("auzix-office-package-smoke")
    commands = "\n".join(str(stage.get("command", "")) for stage in stages)
    assert "auzix-office-smoke.packages" in commands
    assert "office-smoke.report.json" in commands
    assert "run-auzix-office-smoke.sh" in commands
    assert "build-auzix-office-package.sh" not in commands
    assert "test-auzix-office-smoke.sh" in commands
    assert "audit-auzix-package-runtime.sh" in commands
    assert "AbiWord" in commands
    assert "Gnumeric" in commands
    assert "publish-auzix-package-repo.sh" in commands


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

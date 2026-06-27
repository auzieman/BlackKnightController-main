from __future__ import annotations

from services import pipeline_catalog, resource_graph
from services.action_catalog import action_by_id, actions_for_kind, list_actions
from services.pipeline_catalog import pipeline_by_id
from services.pipeline_executor import workflow_stage_definitions
from routes.pipelines import _run_matches_search


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
    kinds = {stage["name"]: stage.get("kind", "remote-command") for stage in stages}
    assert "apt-get update" in commands
    assert "grub2-common grub-pc-bin" in commands
    assert "xinit xserver-xorg-core xserver-xorg-legacy" in commands
    assert "enlightenment terminology" in commands
    assert "lightdm lightdm-gtk-greeter" in commands
    assert "auzix-strict-root" in commands
    assert "auzix-strict-busybox" in commands
    assert "auzix-strict-access" in commands
    assert "auzix-strict-live-tools" in commands
    assert "auzix-strict-installer-test" in commands
    assert "auzix-strict-dbus" in commands
    assert "auzix-strict-udev" in commands
    assert "auzix-strict-acpid" in commands
    assert "auzix-strict-pulseaudio" in commands
    assert "auzix-strict-host-xorg" in commands
    assert "auzix-strict-host-e" in commands
    assert "auzix-strict-lightdm" in commands
    assert "auzix-strict-user-defaults" in commands
    assert "auzix-strict-grub" in commands
    assert "/Programs/Enlightenment/current/Commands/enlightenment_start" in commands
    assert "/Programs/Xorg/current/Commands/Xorg" in commands
    assert 'grep -F "auzix:x:1000:1000:"' in commands
    assert "out/auzix-strict/AuzixRoot/Users/auzix" in commands
    assert "Users/auzix/.config/autostart/auzix-installer.desktop" in commands
    assert "/System/Tools/launch-auzix-installer --autostart" in commands
    assert "out/auzix-strict/AuzixRoot/Programs/Xorg/current" in commands
    assert "out/auzix-strict/AuzixRoot/Programs/Enlightenment/current" in commands
    assert "xorg_current=$(readlink out/auzix-strict/AuzixRoot/Programs/Xorg/current)" in commands
    assert 'AuzixRoot${xorg_current}/Commands/Xorg' in commands
    assert "e_current=$(readlink out/auzix-strict/AuzixRoot/Programs/Enlightenment/current)" in commands
    assert 'AuzixRoot${e_current}/Commands/enlightenment_start' in commands
    assert "Xorg-*.auzix.json" in commands
    assert "Enlightenment-*.auzix.json" in commands
    assert "auzix-strict-audit" not in commands
    assert "grub_current=$(readlink out/auzix-strict/AuzixRoot/Programs/GRUB/current)" in commands
    assert 'AuzixRoot${grub_current}/Resources/i386-pc' in commands
    assert "Programs/GRUB/current/Resources/i386-pc" not in commands
    assert "auzix-strict-desktop-vm134.iso" in commands
    assert "AUZIX_ISO_WORK_DIR=/var/tmp/auzix-iso-vm134" in commands
    assert "scratch=/var/tmp/auzix-vm134-build" in commands
    assert "rsync -a --delete --exclude out/ --exclude artifacts/" in commands
    assert 'docker run --rm -v "$scratch":/workspace -w /workspace' in commands
    assert "-v /mnt/swarm/AuziX/src:/workspace" not in commands
    assert 'rsync -a --delete "$scratch/out/auzix-strict"' not in commands
    assert 'rsync -a "$scratch/artifacts/auzix/"' not in commands
    assert "root@192.168.1.9" not in commands
    assert kinds["iso-publish"] == "auzix-vm134-iso-publish"
    assert kinds["vm-target-verify"] == "auzix-vm134-target-verify"
    assert "--force --bootloader grub" not in commands


def test_auzix_vm135_fresh_install_target_recreates_disposable_vm():
    pipeline = pipeline_by_id("auzix-vm135-fresh-install-target")
    assert pipeline is not None
    assert pipeline["repo"] == "AuziX"
    assert pipeline["resource_class"] == "slow"
    assert pipeline["stages"] == [
        "artifact-verify",
        "iso-publish",
        "vm135-recreate",
        "vm135-start",
        "install-handoff",
    ]
    assert pipeline["source_path"].endswith(
        "dictionaries/pipelines/AuziX_VM135_Fresh_Install_Target/pipeline.json"
    )
    assert "vm135-target-disk" in pipeline["gates"]
    assert {item["id"] for item in pipeline["items"]} >= {
        "vm135-source-artifact",
        "vm135-boot-media",
        "vm135-target-disk",
    }

    stages = workflow_stage_definitions("auzix-vm135-fresh-install-target")
    commands = "\n".join(str(stage.get("command", "")) for stage in stages)
    kinds = {stage["name"]: stage.get("kind", "remote-command") for stage in stages}
    assert "expected=$(awk" not in commands
    assert "root@192.168.1.9" not in commands
    assert "root@192.168.1.10" not in commands
    assert kinds["artifact-verify"] == "auzix-vm135-artifact-verify"
    assert kinds["iso-publish"] == "auzix-vm135-iso-publish"
    assert kinds["vm135-recreate"] == "auzix-vm135-recreate"
    assert kinds["vm135-start"] == "auzix-vm135-start"
    assert "auzix-strict-iso" not in commands


def test_auzix_core_root_validation_is_pre_iso_gate():
    pipeline = pipeline_by_id("auzix-core-root-validation")
    assert pipeline is not None
    assert pipeline["repo"] == "AuZiX"
    assert pipeline["resource_class"] == "slow"
    assert pipeline["stages"] == [
        "source-verify",
        "builder-prepare",
        "core-validation",
        "prompt-report",
    ]
    assert pipeline["source_path"].endswith(
        "dictionaries/pipelines/AuziX_Core_Root_Validation/pipeline.json"
    )
    assert {item["id"] for item in pipeline["items"]} >= {
        "core-root-contract",
        "package-runtime-contract",
        "container-smoke",
        "bounded-triage-prompt",
    }

    stages = workflow_stage_definitions("auzix-core-root-validation")
    commands = "\n".join(str(stage.get("command", "")) for stage in stages)
    assert "grep -Fx e182842 .auzix-commit" in commands
    assert "run-auzix-core-validation.sh" in commands
    assert "AUZIX_CORE_CONTAINER=0 make auzix-core-validation" in commands
    assert "build-auzix-strict-container.sh" in commands
    assert "ollama-prompt.md" in commands
    assert "auzix-vm135" not in commands
    assert "qm " not in commands


def test_resource_graph_sorts_pipeline_tree_by_latest_run(monkeypatch):
    monkeypatch.setattr(resource_graph, "load_integrations", lambda: {})
    monkeypatch.setattr(resource_graph, "load_rules", lambda: {"groups": {}})
    monkeypatch.setattr(
        resource_graph,
        "_snapshot_indexes",
        lambda: {
            "proxmox": {"nodes": {}, "vms": {}, "containers": {}},
            "ansible": {"hosts": {}},
            "docker": {"nodes": {}, "services": {}, "containers": {}},
        },
    )
    monkeypatch.setattr(
        resource_graph,
        "demo_pipelines",
        lambda: [
            {
                "id": "older-pipeline",
                "name": "Older Pipeline",
                "workflow": "older-workflow",
                "repo": "Example",
                "stages": ["one"],
                "actions": [],
            },
            {
                "id": "auzix-vm134-install-refresh",
                "name": "AuziX VM134 Install Refresh",
                "workflow": "auzix-vm134-install-refresh",
                "repo": "AuziX",
                "stages": ["source-verify"],
                "actions": [],
            },
        ],
    )
    monkeypatch.setattr(
        resource_graph,
        "load_runs",
        lambda: [
            {
                "id": "older-run",
                "tenant_slug": "default",
                "workflow": "older-workflow",
                "status": "complete",
                "updated_at": "2026-06-19T00:00:00+00:00",
                "extra": {"pipeline_id": "older-pipeline"},
            },
            {
                "id": "vm134-run",
                "tenant_slug": "default",
                "workflow": "auzix-vm134-install-refresh",
                "status": "failed",
                "updated_at": "2026-06-20T01:30:43+00:00",
                "extra": {"pipeline_id": "auzix-vm134-install-refresh"},
            },
        ],
    )

    graph = resource_graph.build_resource_graph()
    pipeline_group = next(group for group in graph["tree"] if group["kind"] == "pipeline")
    pipeline_ids = [item["id"] for item in pipeline_group["resources"]]

    assert pipeline_ids[0] == "pipeline:auzix-vm134-install-refresh"
    vm134 = graph["resources_by_id"]["pipeline:auzix-vm134-install-refresh"]
    assert vm134["state"] == "failed"
    assert vm134["facts"]["latest status"] == "failed"
    assert any(
        relationship["source_id"] == "pipeline:auzix-vm134-install-refresh"
        and relationship["target_id"] == "action:vm134-run"
        for relationship in graph["relationships"]
    )


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


def test_rx_demo_video_pipeline_set_loads_from_folders():
    expected = {
        "demo-k3s-add-node": 14,
        "demo-swarm-image-registry": 7,
        "rx-demo-k3s-registry-preflight": 4,
        "rx-demo-k3s-deploy": 8,
        "rx-demo-k3s-redeploy-from-git": 6,
        "rx-demo-k3s-undeploy": 5,
    }

    for pipeline_id, item_count in expected.items():
        pipeline = pipeline_by_id(pipeline_id)
        assert pipeline is not None
        assert pipeline["repo"] in {"rx-demo", "lab-k3s", "lab-swarm"}
        assert pipeline["source_path"].endswith("pipeline.json")
        assert len(pipeline.get("items", [])) == item_count

    deploy = pipeline_by_id("rx-demo-k3s-deploy")
    assert deploy is not None
    assert "compose-parity-smoke" in deploy["gates"]
    assert "kubectl.set_image" in deploy["actions"]

    redeploy = pipeline_by_id("rx-demo-k3s-redeploy-from-git")
    assert redeploy is not None
    assert redeploy["workflow"] == "rx-demo-redeploy-from-git-event"
    assert "git.event.record" in redeploy["actions"]

    add_node = pipeline_by_id("demo-k3s-add-node")
    assert add_node is not None
    assert add_node["workflow"] == "demo-k3s-add-node"
    assert "proxmox.vm.clone" in add_node["actions"]
    assert "k3s.agent.install" in add_node["actions"]
    assert "worker-vm-cloned" in add_node["gates"]
    assert "node-ready" in add_node["gates"]
    assert add_node["reset_stages"] == [
        "select-worker",
        "delete-k3s-node",
        "destroy-worker-vm",
        "verify-reset",
    ]
    reset_stages = workflow_stage_definitions("demo-k3s-add-node", action_mode="undeploy")
    assert [stage["name"] for stage in reset_stages] == add_node["reset_stages"]

    registry = pipeline_by_id("demo-swarm-image-registry")
    assert registry is not None
    assert registry["workflow"] == "demo-swarm-image-registry"
    assert "docker.stack.deploy" in registry["actions"]
    assert "k3s-can-pull" in registry["gates"]


def test_pipeline_run_search_matches_catalog_name_for_linked_runs():
    run = {
        "repo": "lab-k3s",
        "workflow": "demo-k3s-add-node",
        "ref": "",
        "commit": "",
        "notes": "",
        "status": "complete",
        "extra": {"pipeline_id": "demo-k3s-add-node"},
    }

    assert _run_matches_search(run, "demo:")
    assert _run_matches_search(run, "k3s add node")


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

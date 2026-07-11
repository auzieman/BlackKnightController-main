from __future__ import annotations

from services import pipeline_catalog, resource_graph
from services.action_catalog import action_by_id, actions_for_kind, list_actions
from services.automation_runs import create_run, default_stages
from services.pipeline_catalog import pipeline_by_id
from services.pipeline_executor import workflow_is_supported, workflow_stage_definitions
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


def test_small_office_foobar_recipe_is_repo_backed():
    pipeline = pipeline_by_id("small-office-foobar-reference")
    assert pipeline is not None
    assert pipeline["repo"] == "BlackKnightController"
    assert pipeline["source_type"] == "repo-folder"
    assert workflow_is_supported("small-office-foobar-reference")
    assert pipeline["targets"]["identity"] == "node:vm:foobar-id-01"
    assert pipeline["targets"]["crm"] == "node:vm:foobar-crm-01"
    assert pipeline["targets"]["tickets"] == "node:vm:foobar-tickets-01"
    assert pipeline["targets"]["windows_helpdesk_01"] == "node:vm:foobar-helpdesk-win-01"
    assert pipeline["targets"]["linux_dev_01"] == "node:vm:foobar-dev-linux-01"
    assert "foo.bar" in pipeline["tags"]
    assert [stage["id"] for stage in pipeline["stages"]] == [
        "load-recipe-intent",
        "plan-identity-storage",
        "plan-crm-intranet",
        "provision-linux-developer-workstations",
        "provision-windows-helpdesk-workstations",
        "personalize-workstations",
        "validate-small-office",
        "render-demo-lifecycle",
    ]


def test_small_office_foobar_app_vm_pipeline_is_runnable():
    pipeline = pipeline_by_id("small-office-foobar-app-vms")
    assert pipeline is not None
    assert pipeline["repo"] == "BlackKnightController"
    assert pipeline["source_type"] == "repo-folder"
    assert workflow_is_supported("small-office-foobar-app-vms")
    assert pipeline["targets"]["crm"] == "node:vm:foobar-crm-01"
    assert pipeline["targets"]["tickets"] == "node:vm:foobar-tickets-01"
    assert "kanboard" in pipeline["tags"]
    assert [stage["id"] for stage in pipeline["stages"]] == [
        "load-app-vm-plan",
        "select-trixie-source",
        "clone-app-vms",
        "boot-app-vms",
        "record-app-relationships",
    ]
    assert default_stages("small-office-foobar-app-vms") == [
        "load-app-vm-plan",
        "select-trixie-source",
        "clone-app-vms",
        "boot-app-vms",
        "record-app-relationships",
    ]
    assert [stage["name"] for stage in workflow_stage_definitions("small-office-foobar-app-vms")] == default_stages(
        "small-office-foobar-app-vms"
    )


def test_small_office_foobar_services_pipeline_is_runnable():
    pipeline = pipeline_by_id("small-office-foobar-services")
    assert pipeline is not None
    assert pipeline["repo"] == "BlackKnightController"
    assert pipeline["source_type"] == "repo-folder"
    assert workflow_is_supported("small-office-foobar-services")
    assert pipeline["targets"]["identity"] == "node:vm:foobar-id-01"
    assert pipeline["targets"]["crm"] == "node:vm:foobar-crm-01"
    assert pipeline["targets"]["tickets"] == "node:vm:foobar-tickets-01"
    assert "ldap" in pipeline["tags"]
    assert [stage["id"] for stage in pipeline["stages"]] == [
        "load-service-plan",
        "ensure-identity-vm",
        "wait-service-guests",
        "install-identity-packages",
        "seed-ldap-directory",
        "configure-samba-homes",
        "publish-identity-portal",
        "provision-crm-mock",
        "provision-ticket-mock",
        "validate-foobar-services",
        "record-service-relationships",
    ]
    assert default_stages("small-office-foobar-services") == [
        "load-service-plan",
        "ensure-identity-vm",
        "wait-service-guests",
        "install-identity-packages",
        "seed-ldap-directory",
        "configure-samba-homes",
        "publish-identity-portal",
        "provision-crm-mock",
        "provision-ticket-mock",
        "validate-foobar-services",
        "record-service-relationships",
    ]
    assert [stage["name"] for stage in workflow_stage_definitions("small-office-foobar-services")] == default_stages(
        "small-office-foobar-services"
    )


def test_small_office_foobar_reset_is_safe_by_default():
    pipeline = pipeline_by_id("small-office-foobar-reset")
    assert pipeline is not None
    assert pipeline["repo"] == "BlackKnightController"
    assert pipeline["source_type"] == "repo-folder"
    assert workflow_is_supported("small-office-foobar-reset")
    assert pipeline["inputs"]["enable_destroy"]["default"] is False
    assert "destroy-disabled-by-default" in {gate["id"] for gate in pipeline["gates"]}
    assert [stage["id"] for stage in pipeline["stages"]] == [
        "load-reset-scope",
        "plan-demo-vm-removal",
        "plan-pxe-route-cleanup",
        "plan-evidence-archive",
        "verify-reset-boundary",
    ]


def test_small_office_foobar_run_stages_are_recording_friendly():
    reference = create_run(
        tenant_slug="default",
        requested_by="test",
        trigger_source="test",
        repo="BlackKnightController",
        workflow="small-office-foobar-reference",
    )
    reset = create_run(
        tenant_slug="default",
        requested_by="test",
        trigger_source="test",
        repo="BlackKnightController",
        workflow="small-office-foobar-reset",
    )

    assert [stage["name"] for stage in reference["stages"]] == default_stages("small-office-foobar-reference")
    assert [stage["name"] for stage in reset["stages"]] == default_stages("small-office-foobar-reset")


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


def test_pipeline_folder_loader_can_use_runtime_mount(monkeypatch, tmp_path):
    pipeline_dir = tmp_path / "runtime-pipelines" / "Runtime_Pipeline"
    pipeline_dir.mkdir(parents=True)
    (pipeline_dir / "pipeline.json").write_text(
        """{
  "id": "runtime-folder-pipeline",
  "name": "Runtime Folder Pipeline",
  "repo": "Runtime",
  "workflow": "candidate-import",
  "description": "Loaded from a runtime mount.",
  "stages": ["preflight"]
}
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("BKC_PIPELINE_FOLDERS_PATH", str(tmp_path / "runtime-pipelines"))

    pipeline = pipeline_catalog.pipeline_by_id("runtime-folder-pipeline")
    assert pipeline is not None
    assert pipeline["source_path"].endswith("Runtime_Pipeline/pipeline.json")
    assert pipeline["source_type"] == "runtime-folder"


def test_pipeline_loader_merges_repo_and_runtime_folder_layers(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo-pipelines" / "Shared_Pipeline"
    runtime_dir = tmp_path / "runtime-pipelines" / "Shared_Pipeline"
    repo_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)

    (repo_dir / "pipeline.json").write_text(
        """{
  "id": "shared-folder-pipeline",
  "name": "Shared Folder Pipeline",
  "repo": "Portable",
  "workflow": "candidate-import",
  "description": "Portable recipe.",
  "stages": ["repo-stage"],
  "tags": ["repo"]
}
""",
        encoding="utf-8",
    )
    (runtime_dir / "pipeline.json").write_text(
        """{
  "id": "shared-folder-pipeline",
  "name": "Shared Folder Pipeline Runtime",
  "repo": "Runtime",
  "workflow": "candidate-import",
  "description": "Runtime override.",
  "stages": ["runtime-stage"],
  "tags": ["runtime"]
}
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("BKC_REPO_PIPELINE_FOLDERS_PATH", str(tmp_path / "repo-pipelines"))
    monkeypatch.setenv("BKC_PIPELINE_FOLDERS_PATH", str(tmp_path / "runtime-pipelines"))

    pipeline = pipeline_catalog.pipeline_by_id("shared-folder-pipeline")
    assert pipeline is not None
    assert pipeline["source_type"] == "runtime-folder"
    assert pipeline["repo"] == "Runtime"
    assert pipeline["stages"] == ["runtime-stage"]
    assert [layer["source_type"] for layer in pipeline["source_layers"]] == [
        "repo-folder",
        "runtime-folder",
    ]


def test_repo_folder_pipeline_loads_without_runtime_override(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo-pipelines" / "Repo_Only"
    repo_dir.mkdir(parents=True)
    (repo_dir / "pipeline.json").write_text(
        """{
  "id": "repo-only-pipeline",
  "name": "Repo Only",
  "repo": "Portable",
  "workflow": "candidate-import",
  "description": "Portable recipe.",
  "stages": ["preflight"]
}
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("BKC_REPO_PIPELINE_FOLDERS_PATH", str(tmp_path / "repo-pipelines"))
    monkeypatch.setenv("BKC_PIPELINE_FOLDERS_PATH", str(tmp_path / "runtime-pipelines"))

    pipeline = pipeline_catalog.pipeline_by_id("repo-only-pipeline")
    assert pipeline is not None
    assert pipeline["source_type"] == "repo-folder"
    assert pipeline["source_layers"][0]["source_path"].endswith("Repo_Only/pipeline.json")


def test_folder_pipeline_defaults_workflow_to_id(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo-pipelines" / "Draft_Recipe"
    repo_dir.mkdir(parents=True)
    (repo_dir / "pipeline.json").write_text(
        """{
  "id": "draft-folder-pipeline",
  "name": "Draft Folder Pipeline",
  "description": "Draft recipe without executor wiring.",
  "stages": ["preflight"]
}
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("BKC_REPO_PIPELINE_FOLDERS_PATH", str(tmp_path / "repo-pipelines"))

    pipeline = pipeline_catalog.pipeline_by_id("draft-folder-pipeline")
    assert pipeline is not None
    assert pipeline["workflow"] == "draft-folder-pipeline"
    assert pipeline["repo"] == "BlackKnightController"
    assert pipeline["description"] == "Draft recipe without executor wiring."


def test_ns1_dhcp_prepare_pipeline_resolves_runtime_dictionary():
    pipeline = pipeline_by_id("ns1-provisioning-dhcp-prepare")
    assert pipeline is not None
    assert pipeline["workflow"] == "ns1-provisioning-dhcp-prepare"
    assert pipeline["repo"] == "BlackKnightController"
    assert pipeline["source_type"] == "runtime-folder"
    assert len(pipeline.get("items", [])) == 9
    assert [layer["source_type"] for layer in pipeline["source_layers"]] == [
        "repo-folder",
        "runtime-folder",
    ]

    resolved = pipeline_catalog.resolve_pipeline_dictionary(pipeline)
    assert resolved["missing"] == []
    assert resolved["values"]["provisioning_interface"] == "ens19"
    assert resolved["values"]["dhcp_range_start"] == "10.20.0.100"
    assert resolved["values"]["dhcp_range_end"] == "10.20.0.120"
    assert resolved["values"]["dhcp_config_path"] == "/etc/dhcp/dhcpd.conf"
    assert resolved["values"]["dhcp_fragment_path"] == "/etc/dhcp/dhcpd.d/bkc-provisioning.conf"
    assert resolved["values"]["dhcp_include_line"] == 'include "/etc/dhcp/dhcpd.d/bkc-provisioning.conf";'
    assert resolved["values"]["enable_dhcp_service"] is False


def test_ns1_trixie_pxe_smoke_pipeline_resolves_runtime_dictionary():
    pipeline = pipeline_by_id("ns1-trixie-pxe-smoke")
    assert pipeline is not None
    assert pipeline["workflow"] == "ns1-trixie-pxe-smoke"
    assert pipeline["repo"] == "BlackKnightController"
    assert pipeline["source_type"] == "runtime-folder"
    assert len(pipeline.get("items", [])) == 15
    assert [layer["source_type"] for layer in pipeline["source_layers"]] == [
        "repo-folder",
        "runtime-folder",
    ]

    resolved = pipeline_catalog.resolve_pipeline_dictionary(pipeline)
    assert resolved["missing"] == []
    assert resolved["values"]["boot_image_name"] == "debian-trixie-amd64-netboot"
    assert resolved["values"]["pxe_http_host"] == "10.20.0.10"
    assert resolved["values"]["target_vmid"] == 132
    assert resolved["values"]["target_vm_name"] == "trixie-smoke-132"
    assert resolved["values"]["target_vm_storage"] == "local-lvm"
    assert resolved["values"]["target_vm_boot_nic"] == "net0"
    assert resolved["values"]["target_vm_boot_bridge"] == "vmbr20"
    assert resolved["values"]["target_vm_management_nic"] == "net1"
    assert resolved["values"]["target_vm_management_bridge"] == "vmbr0"
    assert resolved["values"]["enable_vm_create"] is True
    assert resolved["values"]["enable_pxe_boot"] is True
    assert resolved["values"]["enable_install"] is True


def test_windows10_reference_discover_pipeline_resolves_runtime_dictionary():
    pipeline = pipeline_by_id("windows10-reference-discover")
    assert pipeline is not None
    assert pipeline["workflow"] == "windows10-reference-discover"
    assert pipeline["repo"] == "BlackKnightController"
    assert pipeline["source_type"] == "runtime-folder"
    assert len(pipeline.get("items", [])) == 5
    assert [layer["source_type"] for layer in pipeline["source_layers"]] == [
        "repo-folder",
        "runtime-folder",
    ]

    resolved = pipeline_catalog.resolve_pipeline_dictionary(pipeline)
    assert resolved["missing"] == []
    assert resolved["values"]["windows_iso_name"] == "Win10_22H2_English_x64v1.iso"
    assert resolved["values"]["proxmox_iso_volume"] == "local:iso/Win10_22H2_English_x64.iso"
    assert resolved["values"]["reference_vmid"] == 113
    assert resolved["values"]["reference_vm_ip"] == "192.168.1.90"
    assert resolved["values"]["control_channel"] == "openssh"


def test_windows10_pxe_smoke_pipeline_resolves_runtime_dictionary():
    pipeline = pipeline_by_id("windows10-pxe-smoke")
    assert pipeline is not None
    assert pipeline["workflow"] == "windows10-pxe-smoke"
    assert pipeline["repo"] == "BlackKnightController"
    assert pipeline["source_type"] == "runtime-folder"
    assert len(pipeline.get("items", [])) == 17
    assert [layer["source_type"] for layer in pipeline["source_layers"]] == [
        "repo-folder",
        "runtime-folder",
    ]

    resolved = pipeline_catalog.resolve_pipeline_dictionary(pipeline)
    assert resolved["missing"] == []
    assert resolved["values"]["proxmox_iso_volume"] == "local:iso/Win10_22H2_English_x64.iso"
    assert resolved["values"]["target_vmid"] == 136
    assert resolved["values"]["target_vm_mac"] == "02:10:20:00:01:36"
    assert resolved["values"]["windows_ipxe_script_path"] == "/srv/pxe/windows10.ipxe"
    assert resolved["values"]["windows_media_share"] == "win10media"
    assert resolved["values"]["enable_install"] is True
    assert resolved["values"]["enable_post_install_ssh_check"] is False


def test_windows10_winpe_builder_pipeline_resolves_runtime_dictionary():
    pipeline = pipeline_by_id("windows10-winpe-builder")
    assert pipeline is not None
    assert pipeline["workflow"] == "windows10-winpe-builder"
    assert pipeline["repo"] == "BlackKnightController"
    assert pipeline["source_type"] == "runtime-folder"
    assert len(pipeline.get("items", [])) == 9
    assert [layer["source_type"] for layer in pipeline["source_layers"]] == [
        "repo-folder",
        "runtime-folder",
    ]

    resolved = pipeline_catalog.resolve_pipeline_dictionary(pipeline)
    assert resolved["missing"] == []
    assert resolved["values"]["builder_vmid"] == 113
    assert resolved["values"]["builder_vm_ip"] == "192.168.1.90"
    assert resolved["values"]["winpe_artifact_name"] == "bkc-winpe-amd64"
    assert resolved["values"]["enable_build"] is False


def test_ns1_provisioning_lanes_are_supported_review_workflows():
    network_stages = workflow_stage_definitions("ns1-provisioning-network-prepare")
    dhcp_stages = workflow_stage_definitions("ns1-provisioning-dhcp-prepare")
    trixie_stages = workflow_stage_definitions("ns1-trixie-pxe-smoke")
    windows_stages = workflow_stage_definitions("windows10-reference-discover")
    windows_pxe_stages = workflow_stage_definitions("windows10-pxe-smoke")
    winpe_builder_stages = workflow_stage_definitions("windows10-winpe-builder")
    trixie_personalize_stages = workflow_stage_definitions("trixie-workstation-personalize")
    windows_personalize_stages = workflow_stage_definitions("windows10-workstation-personalize")

    assert workflow_is_supported("ns1-provisioning-network-prepare")
    assert workflow_is_supported("ns1-provisioning-dhcp-prepare")
    assert workflow_is_supported("ns1-trixie-pxe-smoke")
    assert workflow_is_supported("windows10-reference-discover")
    assert workflow_is_supported("windows10-pxe-smoke")
    assert workflow_is_supported("windows10-winpe-builder")
    assert workflow_is_supported("trixie-workstation-personalize")
    assert workflow_is_supported("windows10-workstation-personalize")
    assert [stage["name"] for stage in network_stages] == [
        "resolve-ns1-node",
        "discover-current-network",
        "select-provisioning-interface",
        "apply-provisioning-address",
        "validate-management-still-reachable",
        "record-network-relationships",
    ]
    assert [stage["name"] for stage in dhcp_stages] == [
        "resolve-ns1-node",
        "verify-provisioning-network",
        "ensure-dhcp-include",
        "render-dhcp-fragment",
        "render-dhcp-defaults",
        "install-dhcp-package",
        "validate-dhcp-config",
        "keep-dhcp-disabled",
        "record-dhcp-relationships",
    ]
    assert [stage["name"] for stage in trixie_stages] == [
        "resolve-provisioning-context",
        "verify-ns1-pxe-prereqs",
        "fetch-trixie-netboot",
        "render-ipxe-entry",
        "render-preseed-profile",
        "prepare-vm132-pxe-target",
        "pxe-boot-vm132",
        "observe-installer-handoff",
        "post-boot-recollect",
        "record-trixie-relationships",
    ]
    assert [stage["name"] for stage in windows_stages] == [
        "verify-iso",
        "inspect-vm113",
        "validate-openssh",
        "stage-firstboot-artifacts",
        "record-windows-relationships",
    ]
    assert [stage["name"] for stage in windows_pxe_stages] == [
        "resolve-windows-pxe-context",
        "verify-ns1-pxe-prereqs",
        "verify-windows-iso",
        "fetch-wimboot",
        "stage-windows-install-media",
        "render-windows-ipxe",
        "configure-windows-media-share",
        "render-unattend-firstboot",
        "render-dhcp-windows-route",
        "prepare-vm136-pxe-target",
        "pxe-boot-vm136",
        "observe-winpe-handoff",
        "post-install-ssh-check",
        "record-windows-pxe-relationships",
    ]
    assert [stage["name"] for stage in trixie_personalize_stages] == [
        "discover-installed-trixie",
        "install-workstation-packages",
        "install-vscode-if-enabled",
        "install-rustdesk-if-configured",
        "enable-graphical-services",
        "verify-trixie-personality",
        "record-trixie-personality",
    ]
    assert [stage["name"] for stage in windows_personalize_stages] == [
        "discover-installed-windows",
        "ensure-chocolatey",
        "install-workstation-packages",
        "verify-windows-personality",
        "record-windows-personality",
    ]
    assert [stage["name"] for stage in winpe_builder_stages] == [
        "resolve-builder-context",
        "inspect-vm113",
        "validate-builder-ssh",
        "inspect-adk-tooling",
        "stage-builder-scripts",
        "install-adk-if-enabled",
        "build-winpe-if-enabled",
        "publish-winpe-if-enabled",
        "record-winpe-builder-relationships",
    ]
    assert {stage["kind"] for stage in network_stages + dhcp_stages} == {"folder-pipeline-review"}
    assert {stage["kind"] for stage in trixie_stages} == {
        "event-note",
        "folder-pipeline-review",
        "trixie-ipxe-render",
        "trixie-netboot-fetch",
        "trixie-preseed-render",
        "trixie-pxe-prereqs",
        "trixie-vm-boot",
        "trixie-vm-observe",
        "trixie-vm-prepare",
    }
    assert {stage["kind"] for stage in windows_stages} == {
        "folder-pipeline-review",
        "windows10-inspect-vm",
        "windows10-stage-artifacts",
        "windows10-validate-openssh",
        "windows10-verify-iso",
    }
    assert {stage["kind"] for stage in windows_pxe_stages} == {
        "event-note",
        "folder-pipeline-review",
        "windows10-dhcp-route-render",
        "windows10-ipxe-render",
        "windows10-media-share",
        "windows10-pxe-prereqs",
        "windows10-pxe-verify-iso",
        "windows10-unattend-render",
        "windows10-vm-boot",
        "windows10-vm-observe",
        "windows10-vm-prepare",
        "windows10-wimboot-fetch",
        "windows10-winpe-stage",
    }
    assert {stage["kind"] for stage in trixie_personalize_stages} == {
        "folder-pipeline-review",
        "trixie-personalize-discover",
        "trixie-personalize-packages",
        "trixie-personalize-vscode",
        "trixie-personalize-rustdesk",
        "trixie-personalize-services",
        "trixie-personalize-verify",
    }
    assert {stage["kind"] for stage in windows_personalize_stages} == {
        "folder-pipeline-review",
        "windows10-personalize-discover",
        "windows10-personalize-chocolatey",
        "windows10-personalize-packages",
        "windows10-personalize-verify",
    }
    assert {stage["kind"] for stage in winpe_builder_stages} == {
        "folder-pipeline-review",
        "windows10-adk-inspect",
        "windows10-adk-install",
        "windows10-builder-inspect-vm",
        "windows10-builder-ssh",
        "windows10-builder-stage-scripts",
        "windows10-winpe-build",
        "windows10-winpe-publish",
    }
    assert default_stages("ns1-provisioning-network-prepare") == [
        stage["name"] for stage in network_stages
    ]
    assert default_stages("ns1-provisioning-dhcp-prepare") == [stage["name"] for stage in dhcp_stages]
    assert default_stages("ns1-trixie-pxe-smoke") == [stage["name"] for stage in trixie_stages]
    assert default_stages("windows10-reference-discover") == [stage["name"] for stage in windows_stages]
    assert default_stages("windows10-pxe-smoke") == [stage["name"] for stage in windows_pxe_stages]
    assert default_stages("windows10-winpe-builder") == [
        stage["name"] for stage in winpe_builder_stages
    ]
    assert default_stages("trixie-workstation-personalize") == [
        stage["name"] for stage in trixie_personalize_stages
    ]
    assert default_stages("windows10-workstation-personalize") == [
        stage["name"] for stage in windows_personalize_stages
    ]


def test_runtime_dictionary_folder_can_overlay_repo_pipeline_without_pipeline_json(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo-pipelines" / "Ns1_Recipe"
    runtime_dir = tmp_path / "runtime-pipelines" / "NS1_Runtime"
    items_dir = runtime_dir / "items"
    repo_dir.mkdir(parents=True)
    items_dir.mkdir(parents=True)

    (repo_dir / "pipeline.json").write_text(
        """{
  "id": "ns1-provisioning-network-prepare",
  "name": "ns1 Provisioning Network Prepare",
  "repo": "BlackKnightController",
  "workflow": "ns1-provisioning-network-prepare",
  "description": "Portable recipe.",
  "stages": ["discover-current-network"]
}
""",
        encoding="utf-8",
    )
    (runtime_dir / "dictionary.json").write_text(
        """{
  "pipeline_id": "ns1-provisioning-network-prepare",
  "target_node_id": "node:vm:ns1",
  "provisioning_interface": "ens19"
}
""",
        encoding="utf-8",
    )
    (items_dir / "10-discover-current-network.json").write_text(
        """{
  "name": "Discover Current Network",
  "action": "ssh.network.discover"
}
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("BKC_REPO_PIPELINE_FOLDERS_PATH", str(tmp_path / "repo-pipelines"))
    monkeypatch.setenv("BKC_PIPELINE_FOLDERS_PATH", str(tmp_path / "runtime-pipelines"))

    pipeline = pipeline_catalog.pipeline_by_id("ns1-provisioning-network-prepare")
    assert pipeline is not None
    assert pipeline["source_type"] == "runtime-folder"
    assert pipeline["source_path"].endswith("NS1_Runtime/dictionary.json")
    assert pipeline["dictionary"]["provisioning_interface"] == "ens19"
    assert pipeline["items"][0]["id"] == "10-discover-current-network"
    assert [layer["source_type"] for layer in pipeline["source_layers"]] == [
        "repo-folder",
        "runtime-folder",
    ]

    resolved = pipeline_catalog.resolve_pipeline_dictionary(pipeline)
    assert resolved["values"]["target_node_id"] == "node:vm:ns1"
    assert resolved["values"]["provisioning_interface"] == "ens19"
    assert [layer["name"] for layer in resolved["layers"]] == [
        "runtime dictionary",
    ]


def test_pipeline_dictionary_resolution_merges_repo_defaults_and_runtime(monkeypatch, tmp_path):
    repo_dir = tmp_path / "repo-pipelines" / "Ns1_Recipe"
    runtime_dir = tmp_path / "runtime-pipelines" / "NS1_Runtime"
    repo_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)

    (repo_dir / "pipeline.json").write_text(
        """{
  "id": "ns1-provisioning-network-prepare",
  "name": "ns1 Provisioning Network Prepare",
  "repo": "BlackKnightController",
  "workflow": "ns1-provisioning-network-prepare",
  "description": "Portable recipe.",
  "stages": ["discover-current-network"],
  "inputs": {
    "target_host": {"required": true},
    "provisioning_interface": {"required": true},
    "required_later": {"required": true}
  }
}
""",
        encoding="utf-8",
    )
    (repo_dir / "defaults.json").write_text(
        """{
  "target_host": "ns1.lab.auzietek.com",
  "provisioning_interface": "",
  "provisioning_address": "10.20.0.10/24"
}
""",
        encoding="utf-8",
    )
    (runtime_dir / "dictionary.json").write_text(
        """{
  "pipeline_id": "ns1-provisioning-network-prepare",
  "provisioning_interface": "ens19"
}
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("BKC_REPO_PIPELINE_FOLDERS_PATH", str(tmp_path / "repo-pipelines"))
    monkeypatch.setenv("BKC_PIPELINE_FOLDERS_PATH", str(tmp_path / "runtime-pipelines"))

    pipeline = pipeline_catalog.pipeline_by_id("ns1-provisioning-network-prepare")
    resolved = pipeline_catalog.resolve_pipeline_dictionary(pipeline)

    assert resolved["values"]["target_host"] == "ns1.lab.auzietek.com"
    assert resolved["values"]["provisioning_interface"] == "ens19"
    assert resolved["values"]["provisioning_address"] == "10.20.0.10/24"
    assert resolved["missing"] == ["required_later"]
    assert [layer["name"] for layer in resolved["layers"]] == [
        "repo defaults",
        "runtime dictionary",
    ]


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
        "rx-demo-k3s-deploy": 10,
        "rx-demo-k3s-redeploy-from-git": 12,
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
    assert "kubectl.apply" in deploy["actions"]

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

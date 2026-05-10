import json

from flask import Blueprint, redirect, render_template, request, url_for
from services.access_control import register_inventory_post_guard
from services.rules_store import load_rules, save_rules

builder_blueprint = Blueprint("builder", __name__)
register_inventory_post_guard(builder_blueprint)


def _normalize_text(value: str, default: str = "") -> str:
    cleaned = value.strip()
    return cleaned if cleaned else default


@builder_blueprint.route("/builder", methods=["GET", "POST"])
def builder():
    rules = load_rules()

    if request.method == "POST":
        object_type = request.form.get("object_type", "group")

        if object_type == "group":
            group_name = _normalize_text(request.form.get("group_name", ""))
            if group_name:
                group = rules["groups"].setdefault(group_name, {"locals": {}, "nodes": {}})
                group["locals"] = {
                    "env": _normalize_text(request.form.get("env", ""), "lab"),
                    "datacenter": _normalize_text(request.form.get("datacenter", ""), "homelab"),
                    "release": _normalize_text(request.form.get("release", ""), "draft"),
                    "workflow": _normalize_text(
                        request.form.get("workflow", ""),
                        "build -> provision -> configure -> deploy",
                    ),
                    "provider": _normalize_text(request.form.get("group_provider", ""), "proxmox"),
                    "proxmox_node": _normalize_text(request.form.get("proxmox_node", "")),
                    "template_vmid": _normalize_text(request.form.get("template_vmid", "")),
                }

        if object_type == "node":
            group_name = _normalize_text(request.form.get("target_group", ""))
            host_name = _normalize_text(request.form.get("host_name", ""))
            if group_name and host_name:
                group = rules["groups"].setdefault(group_name, {"locals": {}, "nodes": {}})
                group["nodes"][host_name] = {
                    "user": _normalize_text(request.form.get("user", ""), "root"),
                    "password": _normalize_text(request.form.get("password", "")),
                    "port": int(request.form.get("port", "22") or "22"),
                    "private_key": _normalize_text(request.form.get("private_key", "")),
                    "provider": _normalize_text(request.form.get("provider", ""), "proxmox"),
                    "ip": _normalize_text(request.form.get("ip", "")),
                    "vmid": _normalize_text(request.form.get("vmid", "")),
                    "provisioner": _normalize_text(request.form.get("provisioner", ""), "cloud-init"),
                    "configuration": _normalize_text(request.form.get("configuration", ""), "ansible"),
                    "state": _normalize_text(request.form.get("state", ""), "planned"),
                    "application": _normalize_text(request.form.get("application", ""), "bkc-managed"),
                }

        save_rules(rules)
        return redirect(url_for("builder.builder"))

    inventory_json = json.dumps(rules, indent=2)
    group_names = sorted(rules["groups"].keys())
    return render_template(
        "builder.html.j2",
        rules=rules,
        inventory_json=inventory_json,
        group_names=group_names,
    )

from flask import Blueprint, abort, redirect, render_template, request, url_for
from services.access_control import register_inventory_post_guard
from services.rules_store import get_templates_path

templates = Blueprint("templates", __name__)
register_inventory_post_guard(templates)


@templates.route("/templates", methods=["GET"])
def list_templates():
    template_dir = get_templates_path()
    template_names = sorted(path.name for path in template_dir.glob("*") if path.is_file())
    return render_template("templates.html.j2", templates=template_names)


@templates.route("/template/<template_name>", methods=["GET", "POST"])
def edit_template(template_name):
    template_path = get_templates_path() / template_name
    if not template_path.exists() or not template_path.is_file():
        abort(404)

    if request.method == "POST":
        template_path.write_text(request.form["content"], encoding="utf-8")
        return redirect(url_for("templates.list_templates"))

    content = template_path.read_text(encoding="utf-8")
    return render_template("edit_templates.html.j2", template=template_name, content=content)

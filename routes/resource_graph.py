from flask import Blueprint, render_template, request

from services.resource_graph import RESOURCE_KIND_META, build_resource_graph, related_to


resource_graph_blueprint = Blueprint("resource_graph", __name__)


@resource_graph_blueprint.route("/resources", methods=["GET"])
def resource_graph():
    graph = build_resource_graph()
    tab = request.args.get("tab", "summary").strip().lower()
    if tab not in {"summary", "relationships", "actions", "inventory"}:
        tab = "summary"

    selected_id = request.args.get("resource", "").strip()
    selected_kind = request.args.get("kind", "").strip().lower()
    search = request.args.get("q", "").strip().lower()

    visible_tree = []
    for group in graph["tree"]:
        resources = group["resources"]
        if selected_kind and group["kind"] != selected_kind:
            resources = []
        if search:
            resources = [
                resource
                for resource in resources
                if search in resource["name"].lower()
                or search in resource["kind"].lower()
                or search in resource.get("summary", "").lower()
                or any(search in str(value).lower() for value in resource.get("facts", {}).values())
            ]
        if resources:
            visible_tree.append({**group, "resources": resources})

    visible_resources = [resource for group in visible_tree for resource in group["resources"]]
    if selected_id not in graph["resources_by_id"] and visible_resources:
        selected_id = visible_resources[0]["id"]
    selected = graph["resources_by_id"].get(selected_id)

    return render_template(
        "resource_graph.html.j2",
        graph=graph,
        kind_meta=RESOURCE_KIND_META,
        visible_tree=visible_tree,
        visible_resources=visible_resources,
        selected=selected,
        selected_id=selected_id,
        selected_kind=selected_kind,
        tab=tab,
        search=search,
        relationships=related_to(graph, selected_id) if selected else [],
    )

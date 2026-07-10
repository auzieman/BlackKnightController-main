from services.resource_graph import cytoscape_elements_from_resource_graph


def test_cytoscape_elements_include_compound_nodes_and_normalized_edges():
    graph = {
        "resources": [
            {
                "id": "host:pve1",
                "kind": "host",
                "name": "pve1",
                "state": "known",
                "facts": {},
            },
            {
                "id": "vm:trixie-smoke-132",
                "kind": "vm",
                "name": "trixie-smoke-132",
                "state": "running",
                "facts": {"proxmox node": "pve1"},
            },
            {
                "id": "container:blackknight_bkc",
                "kind": "container",
                "name": "blackknight_bkc",
                "state": "failed",
                "facts": {"proxmox node": "pve1"},
            },
            {
                "id": "pipeline:ns1-trixie-pxe-smoke",
                "kind": "pipeline",
                "name": "ns1 Trixie PXE Smoke",
                "state": "complete",
                "facts": {},
            },
            {
                "id": "repo:rx-demo",
                "kind": "repo",
                "name": "rx-demo",
                "state": "known",
                "facts": {},
            },
        ],
        "resources_by_id": {
            "host:pve1": {"kind": "host", "name": "pve1"},
            "vm:trixie-smoke-132": {"kind": "vm", "name": "trixie-smoke-132"},
            "container:blackknight_bkc": {"kind": "container", "name": "blackknight_bkc"},
            "pipeline:ns1-trixie-pxe-smoke": {"kind": "pipeline", "name": "ns1 Trixie PXE Smoke"},
            "repo:rx-demo": {"kind": "repo", "name": "rx-demo"},
        },
        "relationships": [
            {
                "source_id": "host:pve1",
                "target_id": "vm:trixie-smoke-132",
                "type": "contains",
            },
            {
                "source_id": "pipeline:ns1-trixie-pxe-smoke",
                "target_id": "vm:trixie-smoke-132",
                "type": "targets",
            },
            {
                "source_id": "pipeline:ns1-trixie-pxe-smoke",
                "target_id": "repo:rx-demo",
                "type": "uses",
            },
        ],
    }

    elements = cytoscape_elements_from_resource_graph(graph)
    nodes = {node["data"]["id"]: node["data"] for node in elements["nodes"]}

    assert set(elements) == {"nodes", "edges"}
    assert nodes["vm:trixie-smoke-132"] == {
        "id": "vm:trixie-smoke-132",
        "label": "trixie-smoke-132",
        "type": "vm",
        "status": "running",
        "parent": "host:pve1",
    }
    assert nodes["container:blackknight_bkc"]["status"] == "failed"
    assert "repo:rx-demo" not in nodes
    assert all(set(edge["data"]) == {"id", "source", "target", "type"} for edge in elements["edges"])
    assert {
        (edge["data"]["source"], edge["data"]["target"], edge["data"]["type"])
        for edge in elements["edges"]
    } >= {
        ("host:pve1", "vm:trixie-smoke-132", "dependency"),
        ("host:pve1", "container:blackknight_bkc", "dependency"),
    }
    assert {
        edge["data"]["type"]
        for edge in elements["edges"]
    } == {"dependency"}

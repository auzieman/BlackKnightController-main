from __future__ import annotations

import json
import re
import shlex

from services.remote_ops import run_remote_command


class AIGraphLayoutError(RuntimeError):
    pass


def _node_brief(node: dict) -> dict:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    position = node.get("position") if isinstance(node.get("position"), dict) else {}
    return {
        "id": str(data.get("id") or ""),
        "label": str(data.get("label") or ""),
        "type": str(data.get("type") or ""),
        "status": str(data.get("status") or ""),
        "parent": str(data.get("parent") or ""),
        "role": str(data.get("layoutRole") or ""),
        "kind": str(data.get("kind") or ""),
        "importance": str(data.get("layoutImportance") or data.get("importance") or ""),
        "x": position.get("x"),
        "y": position.get("y"),
    }


def _edge_brief(edge: dict) -> dict:
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    return {
        "source": str(data.get("source") or ""),
        "target": str(data.get("target") or ""),
        "type": str(data.get("type") or ""),
    }


def _extract_json_object(text: str) -> dict:
    candidate = (text or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.I)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise AIGraphLayoutError("Ollama response did not contain a JSON object.")
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AIGraphLayoutError(f"Ollama response JSON could not be parsed: {exc}") from exc


def _fallback_position(index: int, total: int, *, width: float, height: float) -> dict[str, float]:
    columns = max(1, min(8, int(total**0.5) + 1))
    row, column = divmod(index, columns)
    x_gap = width / (columns + 1)
    y_gap = height / ((total // columns) + 2)
    return {"x": round((column + 1) * x_gap, 2), "y": round((row + 1) * y_gap, 2)}


def _current_position_map(nodes: list[dict]) -> dict[str, dict[str, float]]:
    positions: dict[str, dict[str, float]] = {}
    for node in nodes:
        node_id = str(node.get("id") or "").strip()
        try:
            x = float(node.get("x"))
            y = float(node.get("y"))
        except (TypeError, ValueError):
            continue
        if node_id:
            positions[node_id] = {"x": x, "y": y}
    return positions


def _validate_positions(
    raw: object,
    valid_ids: list[str],
    *,
    width: float,
    height: float,
    current_positions: dict[str, dict[str, float]] | None = None,
) -> list[dict]:
    if isinstance(raw, dict):
        raw_positions = raw.get("positions")
    else:
        raw_positions = raw
    if not isinstance(raw_positions, list):
        raise AIGraphLayoutError("Ollama layout response must include a positions array.")
    positions: list[dict] = []
    seen: set[str] = set()
    valid_id_set = set(valid_ids)
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("id") or item.get("node_id") or "").strip()
        if node_id not in valid_id_set or node_id in seen:
            continue
        try:
            x = float(item["x"])
            y = float(item["y"])
        except (KeyError, TypeError, ValueError):
            continue
        x = max(0.0, min(width, x))
        y = max(0.0, min(height, y))
        positions.append({"id": node_id, "x": round(x, 2), "y": round(y, 2)})
        seen.add(node_id)
    if len(positions) < 3:
        raise AIGraphLayoutError(f"Ollama returned too few valid positions: {len(positions)}.")
    for index, node_id in enumerate(valid_ids):
        if node_id in seen:
            continue
        fallback = (current_positions or {}).get(node_id) or _fallback_position(
            index,
            len(valid_ids),
            width=width,
            height=height,
        )
        positions.append({"id": node_id, **fallback})
    return positions


def propose_cytoscape_layout(
    elements: dict,
    *,
    host: str = "10.20.0.240",
    model: str = "qwen2.5-coder:1.5b",
    width: float = 1800,
    height: float = 1200,
    max_nodes: int = 90,
) -> dict:
    nodes = [_node_brief(node) for node in elements.get("nodes", []) if isinstance(node, dict)]
    nodes = [node for node in nodes if node["id"]]
    edges = [_edge_brief(edge) for edge in elements.get("edges", []) if isinstance(edge, dict)]
    valid_ids = [node["id"] for node in nodes[:max_nodes]]
    valid_id_set = set(valid_ids)
    scoped_edges = [
        edge for edge in edges if edge["source"] in valid_id_set and edge["target"] in valid_id_set
    ][: max_nodes * 2]
    if not valid_ids:
        raise AIGraphLayoutError("No Cytoscape nodes available for AI layout.")

    graph_payload = {
        "canvas": {"width": width, "height": height},
        "layout_contract": {
            "style": "company-mind mindmap",
            "scope": "recent visible operational graph, not the entire database",
            "lanes": [
                "left: internet, public/remote, unmanaged edge",
                "center: firewall, switch, ns1, IPMI evidence",
                "right: hypervisors, OpenStack, ESXi, Proxmox, active services",
                "lower: recent pipeline/action path when present",
            ],
            "rules": [
                "keep explicit anchor/important nodes stable and prominent",
                "minimize edge crossings",
                "prefer readable branches over circular force-layout blobs",
                "keep children close to their parent but do not overlap labels",
                "place stale/offline items lower or slightly outside the main path",
            ],
        },
        "nodes": nodes[:max_nodes],
        "edges": scoped_edges,
    }
    prompt = (
        "You are laying out a Cytoscape infrastructure graph. "
        "Return ONLY compact JSON with this shape: "
        "{\"positions\":[{\"id\":\"node-id\",\"x\":123,\"y\":456}],\"notes\":\"short\"}. "
        "Use every node id exactly once if practical. Keep related nodes near each other. "
        f"You must only use these exact ids: {json.dumps(valid_ids)}. "
        "Make the result look like a clean Mermaid mindmap or operations architecture sketch, "
        "not a circular force graph. Place pipelines left-to-right, physical edge/fabric/core in the center, "
        "platforms and active services to the right, and remote/public systems above. "
        "Keep coordinates inside the canvas. Do not invent ids.\n\n"
        f"GRAPH:\n{json.dumps(graph_payload, separators=(',', ':'))}"
    )
    ollama_body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_ctx": 8192},
    }
    command = (
        "set -euo pipefail; "
        f"curl -fsS http://127.0.0.1:11434/api/generate -d {shlex.quote(json.dumps(ollama_body))}"
    )
    output = run_remote_command(host=host, user="root", command=command, timeout=240)
    try:
        response = json.loads(output)
    except json.JSONDecodeError as exc:
        raise AIGraphLayoutError(f"Ollama API response was not JSON: {exc}") from exc
    proposed = _extract_json_object(str(response.get("response") or ""))
    positions = _validate_positions(
        proposed,
        valid_ids,
        width=width,
        height=height,
        current_positions=_current_position_map(nodes),
    )
    return {
        "status": "ok",
        "model": model,
        "positions": positions,
        "notes": str(proposed.get("notes") or "")[:500] if isinstance(proposed, dict) else "",
        "node_count": len(valid_ids),
        "position_count": len(positions),
    }

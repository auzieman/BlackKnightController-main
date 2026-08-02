from __future__ import annotations

import hashlib
import json
import time
import xml.etree.ElementTree as ET
from html import escape
from pathlib import Path


TYPE_STYLE = {
    "firewall": ("#f8cecc", "#b85450"),
    "isp": ("#f5f5f5", "#666666"),
    "switch": ("#d5e8d4", "#82b366"),
    "bmc": ("#e1d5e7", "#9673a6"),
    "host": ("#dae8fc", "#6c8ebf"),
    "cluster": ("#ffe6cc", "#d79b00"),
    "remote-site": ("#e1d5e7", "#9673a6"),
    "service": ("#d5e8d4", "#82b366"),
    "vm": ("#d9eaf7", "#3c78d8"),
    "container": ("#d5e8d4", "#82b366"),
    "stack": ("#e1d5e7", "#9673a6"),
    "evidence": ("#fff2cc", "#d6b656"),
    "interface": ("#dae8fc", "#6c8ebf"),
    "pipeline": ("#ffe6cc", "#d79b00"),
    "stage": ("#fff2cc", "#d6b656"),
}


def _cell(root: ET.Element, cell_id: str, value: str = "", style: str = "", parent: str = "1", *, vertex: bool = False, edge: bool = False, source: str = "", target: str = "") -> ET.Element:
    attrs = {"id": cell_id, "parent": parent}
    if value:
        attrs["value"] = value
    if style:
        attrs["style"] = style
    if vertex:
        attrs["vertex"] = "1"
    if edge:
        attrs["edge"] = "1"
    if source:
        attrs["source"] = source
    if target:
        attrs["target"] = target
    return ET.SubElement(root, "mxCell", attrs)


def _geom(parent: ET.Element, x: float, y: float, width: float, height: float, as_: str = "geometry") -> None:
    ET.SubElement(parent, "mxGeometry", {
        "x": str(round(x, 2)),
        "y": str(round(y, 2)),
        "width": str(round(width, 2)),
        "height": str(round(height, 2)),
        "as": as_,
    })


def _node_style(node_type: str, status: str) -> str:
    fill, stroke = TYPE_STYLE.get(node_type, ("#ffffff", "#64748b"))
    if status in {"failed", "offline", "down", "critical"}:
        stroke = "#b85450"
    elif status in {"warning", "stale", "degraded"}:
        stroke = "#d79b00"
    return (
        "rounded=1;whiteSpace=wrap;html=1;arcSize=12;shadow=1;"
        f"fillColor={fill};strokeColor={stroke};fontColor=#1f2937;fontSize=12;"
    )


def _edge_style(edge_type: str) -> str:
    color = "#6c8ebf"
    if edge_type in {"affects", "pipeline_flow"}:
        color = "#d79b00"
    elif edge_type in {"protects", "routes_to", "edge_to"}:
        color = "#b85450"
    elif edge_type in {"describes", "records", "protected_by"}:
        color = "#9673a6"
    return (
        "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;"
        f"html=1;strokeColor={color};fontColor={color};fontSize=10;endArrow=block;"
    )


def export_visible_scene(payload: dict, output_dir: Path, *, tenant_slug: str = "lab") -> dict:
    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    edges = payload.get("edges") if isinstance(payload.get("edges"), list) else []
    scene_hash = hashlib.sha256(json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    file_name = f"bkc-resource-scene-{tenant_slug}-{stamp}-{scene_hash}.drawio"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / file_name

    mxfile = ET.Element("mxfile", {
        "host": "app.diagrams.net",
        "modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agent": "BlackKnightController /resources scene exporter",
        "version": "24.7.17",
    })
    diagram = ET.SubElement(mxfile, "diagram", {"id": scene_hash, "name": "BKC Resource Scene"})
    model = ET.SubElement(diagram, "mxGraphModel", {
        "dx": "1800",
        "dy": "1200",
        "grid": "1",
        "gridSize": "10",
        "guides": "1",
        "tooltips": "1",
        "connect": "1",
        "arrows": "1",
        "fold": "1",
        "page": "1",
        "pageScale": "1",
        "pageWidth": "1800",
        "pageHeight": "1200",
        "math": "0",
        "shadow": "0",
    })
    root = ET.SubElement(model, "root")
    _cell(root, "0", parent="")
    _cell(root, "1", parent="0")

    title = _cell(root, "title", f"<b>BKC Resource Scene</b><br><font style='font-size:11px;color:#64748b'>scene {scene_hash} · exported from /resources</font>", "text;html=1;strokeColor=none;fillColor=none;fontSize=24;fontColor=#0f172a;align=left;", vertex=True)
    _geom(title, 40, 24, 900, 60)

    valid_ids: set[str] = set()
    for index, node in enumerate(nodes[:160]):
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        node_id = str(data.get("id") or "").strip()
        if not node_id:
            continue
        valid_ids.add(node_id)
        pos = node.get("position") if isinstance(node.get("position"), dict) else {}
        x = float(pos.get("x") or 100 + (index % 8) * 210)
        y = float(pos.get("y") or 120 + (index // 8) * 110)
        label = escape(str(data.get("label") or data.get("name") or node_id)).replace("\n", "<br>")
        kind = escape(str(data.get("kind") or data.get("type") or "resource"))
        status = escape(str(data.get("status") or data.get("state") or "unknown"))
        value = f"<b>{label}</b><br><font style='font-size:10px;color:#52606d'>{kind} · {status}</font>"
        cell = _cell(root, f"node:{node_id}", value, _node_style(str(data.get("type") or ""), status), vertex=True)
        _geom(cell, x + 60, y + 90, 170, 62)

    for index, edge in enumerate(edges[:260]):
        data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
        source = str(data.get("source") or "")
        target = str(data.get("target") or "")
        if source not in valid_ids or target not in valid_ids:
            continue
        label = escape(str(data.get("label") or data.get("type") or ""))
        cell = _cell(root, f"edge:{index}:{source}:{target}", label, _edge_style(str(data.get("type") or "")), edge=True, source=f"node:{source}", target=f"node:{target}")
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})

    ET.ElementTree(mxfile).write(output_path, encoding="utf-8", xml_declaration=True)
    return {
        "status": "ready",
        "scene_hash": scene_hash,
        "node_count": len(valid_ids),
        "edge_count": min(len(edges), 260),
        "artifact_path": str(output_path),
        "artifact_url": f"/static/exports/{file_name}",
    }

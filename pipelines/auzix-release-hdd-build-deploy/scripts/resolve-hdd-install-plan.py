#!/usr/bin/env python3
"""Resolve one immutable AUZiX HDD package transaction without installing it."""

import argparse
import hashlib
import json
from pathlib import Path


def load_repo(path: Path, label: str):
    data = json.loads((path / "index.json").read_text())
    for item in data.get("packages", data if isinstance(data, list) else []):
        yield dict(item), path, label


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--r5", type=Path, required=True)
    parser.add_argument("--composite", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--writer", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    candidates = {}
    for repo, label in ((args.native, "native"), (args.composite, "composite"), (args.r5, "r5")):
        for record in load_repo(repo, label):
            candidates.setdefault(record[0]["name"].casefold(), []).append(record)
    for entry in sorted((args.writer / "entries").glob("*.json")):
        item = json.loads(entry.read_text())
        candidates.setdefault(item["name"].casefold(), []).append((item, args.writer, "writer"))

    pins = {
        "busybox": ("composite", "BusyBox", "1.36.1"),
        "gcc14base": ("r5", "GCC14Base", "14.2.0-19"),
        "libgccs1": ("r5", "LibgccS1", "14.2.0-19"),
        "libreofficewriter": ("writer", "LibreOfficeWriter", None),
    }

    def choose(name):
        key = str(name).casefold()
        choices = candidates.get(key, [])
        if key in pins:
            label, exact_name, version = pins[key]
            matches = [r for r in choices if r[2] == label and r[0]["name"] == exact_name and
                       (version is None or str(r[0].get("version")) == version)]
            if len(matches) != 1:
                raise SystemExit(f"provider pin did not resolve exactly once: {name} matches={len(matches)}")
            return matches[0]
        return choices[-1] if choices else None

    roots = []
    for raw in args.profile.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not (line.startswith("[") and line.endswith("]")):
            roots.append(line)

    external = {"libc6": {
        "provider": "compatibility-repair",
        "reason": "No validated Libc6 archive exists in the frozen source catalogs",
        "future_owner": "Libc6",
    }}
    seen, active, active_stack, order, missing, cycle_edges = set(), set(), [], [], [], []

    def visit(name, parent="profile"):
        key = str(name).casefold()
        if key in seen or key in external:
            return
        if key in active:
            cycle_edges.append({
                "from": parent,
                "to": str(name),
                "active_path": list(active_stack),
                "policy": "unpack in deterministic depth-first order; configure/finalize after closure",
            })
            return
        record = choose(name)
        if record is None:
            missing.append({"name": str(name), "required_by": parent})
            return
        active.add(key)
        active_stack.append(record[0]["name"])
        for dependency in record[0].get("depends") or []:
            visit(dependency, record[0]["name"])
        active_stack.pop()
        active.remove(key)
        seen.add(key)
        order.append(record)

    for root in roots:
        visit(root)
    if missing:
        raise SystemExit("missing dependency edges: " + json.dumps(missing[:60], sort_keys=True))

    packages, archive_bytes, identities = [], 0, set()
    for sequence, (item, repo, label) in enumerate(order, 1):
        archive = repo / "packages" / Path(item["package"]).name
        if not archive.is_file():
            raise SystemExit(f"archive missing: {archive}")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if item.get("sha256") and item["sha256"] != digest:
            raise SystemExit(f"hash mismatch: {item['name']}")
        identity = item["name"].casefold()
        if identity in identities:
            raise SystemExit(f"duplicate selected identity: {item['name']}")
        identities.add(identity)
        archive_bytes += archive.stat().st_size
        packages.append({
            "sequence": sequence,
            "name": item["name"],
            "version": str(item.get("version", "")),
            "package": Path(item["package"]).name,
            "sha256": digest,
            "archive_bytes": archive.stat().st_size,
            "source": label,
            "depends": item.get("depends") or [],
        })

    plan = {
        "format": "auzix-hdd-install-plan-v1",
        "profile": str(args.profile),
        "roots": roots,
        "root_count": len(roots),
        "package_count": len(packages),
        "archive_bytes": archive_bytes,
        "provider_pins": {key: {"source": value[0], "name": value[1], "version": value[2]}
                          for key, value in sorted(pins.items())},
        "external_providers": external,
        "dependency_cycles": cycle_edges,
        "packages": packages,
    }
    plan["plan_content_sha256"] = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    encoded = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

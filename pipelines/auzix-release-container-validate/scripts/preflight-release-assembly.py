#!/usr/bin/env python3
"""Reject release assemblies whose package/runtime metadata is not final."""

import argparse
import json
from pathlib import Path


EXTERNAL_BASE_PROVIDERS = {"libc6"}
VALIDATION_ROOTS = (
    "Busybox",
    "NcursesBase",
    "NcursesTerm",
    "Python3",
    "Glances",
    "Htop",
    "LibreOfficeWriter",
)
RUNTIME_SURFACE_PACKAGES = {"libc6", "libgccs1", "gcc14base"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("index", type=Path)
    args = parser.parse_args()

    packages = json.loads(args.index.read_text(encoding="utf-8")).get("packages", [])
    by_name = {str(item["name"]).casefold(): item for item in packages}
    seen: set[str] = set()
    active: set[str] = set()

    def visit(name: str, parent: str = "selection") -> None:
        key = name.casefold()
        if key in seen or key in active or key in EXTERNAL_BASE_PROVIDERS:
            return
        item = by_name.get(key)
        if item is None:
            raise SystemExit(f"missing frozen dependency: {name} required_by={parent}")
        active.add(key)
        for dependency in item.get("depends") or []:
            visit(str(dependency), str(item["name"]))
        active.remove(key)
        seen.add(key)

    for root in VALIDATION_ROOTS:
        visit(root)

    writer = by_name.get("libreofficewriter")
    if writer is None:
        raise SystemExit("LibreOfficeWriter is absent from the frozen release")

    writer_closure: set[str] = set()
    writer_active: set[str] = set()

    def writer_visit(name: str) -> None:
        key = name.casefold()
        if key in writer_closure or key in writer_active or key in EXTERNAL_BASE_PROVIDERS:
            return
        item = by_name.get(key)
        if item is None:
            raise SystemExit(f"LibreOfficeWriter closure is missing {name}")
        writer_active.add(key)
        for dependency in item.get("depends") or []:
            writer_visit(str(dependency))
        writer_active.remove(key)
        writer_closure.add(key)

    for dependency in writer.get("depends") or []:
        writer_visit(str(dependency))

    declared = {
        str(name).casefold()
        for name in ((writer.get("runtime_ladder") or {}).get("dependency_packages") or [])
    }
    expected = writer_closure - RUNTIME_SURFACE_PACKAGES
    missing = sorted(expected - declared)
    extra = sorted(declared - expected)
    if missing or extra:
        raise SystemExit(
            "LibreOfficeWriter runtime ladder is not finalized against the frozen repository: "
            f"missing={','.join(missing[:30]) or '-'} extra={','.join(extra[:30]) or '-'}"
        )

    print(
        f"provider/closure preflight passed: selected={len(seen)} "
        f"writer_runtime_dependencies={len(expected)} external_provider=Libc6"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

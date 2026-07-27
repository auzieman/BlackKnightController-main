#!/usr/bin/env python3
"""Tiny IONOS DNS API helper for BKC lab DNS and ACME DNS-01 hooks.

The API key is read from IONOS_API_KEY or from a file passed with
--api-key-file. It must not be committed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


BASE_URL = "https://api.hosting.ionos.com/dns/v1"


class IonosError(RuntimeError):
    pass


@dataclass
class IonosDns:
    api_key: str
    base_url: str = BASE_URL

    def request(self, method: str, path: str, payload: Any | None = None) -> Any:
        body = None
        headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method.upper(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                text = response.read().decode("utf-8")
                if not text:
                    return None
                return json.loads(text)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise IonosError(f"IONOS {method} {path} failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise IonosError(f"IONOS {method} {path} failed: {exc}") from exc

    def zones(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/zones")
        if not isinstance(data, list):
            raise IonosError("IONOS zones response was not a list")
        return data

    def find_zone(self, fqdn: str) -> dict[str, Any]:
        name = fqdn.rstrip(".").lower()
        zones = self.zones()
        matches = []
        for zone in zones:
            zone_name = str(zone.get("name") or "").rstrip(".").lower()
            if name == zone_name or name.endswith(f".{zone_name}"):
                matches.append((len(zone_name), zone))
        if not matches:
            raise IonosError(f"No IONOS zone owns {fqdn}")
        return sorted(matches, key=lambda item: item[0], reverse=True)[0][1]

    def zone_detail(self, zone_id: str) -> dict[str, Any]:
        data = self.request("GET", f"/zones/{urllib.parse.quote(zone_id)}")
        if not isinstance(data, dict):
            raise IonosError(f"IONOS zone {zone_id} response was not an object")
        return data

    def records(self, fqdn: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        zone = self.find_zone(fqdn)
        zone_id = str(zone.get("id") or "")
        detail = self.zone_detail(zone_id)
        records = detail.get("records")
        if not isinstance(records, list):
            records = []
        return detail, records

    def create_record(self, zone_id: str, record: dict[str, Any]) -> Any:
        return self.request("POST", f"/zones/{urllib.parse.quote(zone_id)}/records", [record])

    def patch_record(self, zone_id: str, record_id: str, record: dict[str, Any]) -> Any:
        return self.request(
            "PATCH",
            f"/zones/{urllib.parse.quote(zone_id)}/records/{urllib.parse.quote(record_id)}",
            record,
        )

    def delete_record(self, zone_id: str, record_id: str) -> Any:
        return self.request(
            "DELETE",
            f"/zones/{urllib.parse.quote(zone_id)}/records/{urllib.parse.quote(record_id)}",
        )


def read_api_key(args: argparse.Namespace) -> str:
    if args.api_key_file:
        return open(args.api_key_file, "r", encoding="utf-8").read().strip()
    key = os.environ.get("IONOS_API_KEY", "").strip()
    if key:
        return key
    raise SystemExit("Missing IONOS API key. Set IONOS_API_KEY or pass --api-key-file.")


def find_matching_records(records: list[dict[str, Any]], name: str, record_type: str, content: str | None = None) -> list[dict[str, Any]]:
    wanted_name = name.rstrip(".").lower()
    wanted_type = record_type.upper()
    matches = []
    for record in records:
        if str(record.get("name") or "").rstrip(".").lower() != wanted_name:
            continue
        if str(record.get("type") or "").upper() != wanted_type:
            continue
        if content is not None and str(record.get("content") or "") != content:
            continue
        matches.append(record)
    return matches


def command_list_zones(client: IonosDns, _args: argparse.Namespace) -> None:
    safe = [
        {
            "id": zone.get("id"),
            "name": zone.get("name"),
            "type": zone.get("type"),
        }
        for zone in client.zones()
    ]
    print(json.dumps(safe, indent=2, sort_keys=True))


def command_snapshot(client: IonosDns, args: argparse.Namespace) -> None:
    zone, records = client.records(args.scope)
    scope = args.scope.rstrip(".").lower()
    filtered = []
    for record in records:
        name = str(record.get("name") or "").rstrip(".").lower()
        if name == scope or name.endswith(f".{scope}"):
            filtered.append(record)
    print(
        json.dumps(
            {
                "zone": {"id": zone.get("id"), "name": zone.get("name"), "type": zone.get("type")},
                "scope": args.scope,
                "records": filtered,
            },
            indent=2,
            sort_keys=True,
        )
    )


def command_ensure_record(client: IonosDns, args: argparse.Namespace) -> None:
    zone, records = client.records(args.name)
    zone_id = str(zone.get("id") or "")
    record = {
        "name": args.name.rstrip("."),
        "type": args.type.upper(),
        "content": args.content,
        "ttl": int(args.ttl),
        "disabled": False,
    }
    if args.type.upper() in {"MX", "SRV"} and args.prio is not None:
        record["prio"] = int(args.prio)
    matches = find_matching_records(records, args.name, args.type)
    exact = [item for item in matches if str(item.get("content") or "") == args.content and int(item.get("ttl") or args.ttl) == int(args.ttl)]
    if exact:
        print(json.dumps({"status": "unchanged", "record": exact[0]}, indent=2, sort_keys=True))
        return
    if matches and not args.allow_multiple:
        result = client.patch_record(zone_id, str(matches[0].get("id")), record)
        print(json.dumps({"status": "updated", "result": result}, indent=2, sort_keys=True))
        return
    result = client.create_record(zone_id, record)
    print(json.dumps({"status": "created", "result": result}, indent=2, sort_keys=True))


def command_ensure_txt(client: IonosDns, args: argparse.Namespace) -> None:
    args.type = "TXT"
    args.allow_multiple = True
    command_ensure_record(client, args)
    if args.propagation_seconds:
        time.sleep(int(args.propagation_seconds))


def command_delete_txt(client: IonosDns, args: argparse.Namespace) -> None:
    zone, records = client.records(args.name)
    zone_id = str(zone.get("id") or "")
    matches = find_matching_records(records, args.name, "TXT", args.content)
    deleted = []
    for record in matches:
        record_id = str(record.get("id") or "")
        if not record_id:
            continue
        client.delete_record(zone_id, record_id)
        deleted.append(record_id)
    print(json.dumps({"status": "deleted", "record_ids": deleted}, indent=2, sort_keys=True))


def load_desired_records(path: str) -> list[dict[str, Any]]:
    payload = json.load(open(path, "r", encoding="utf-8"))
    records = payload.get("desired_records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise SystemExit("Desired map must be a list or an object with desired_records.")
    return records


def command_apply_map(client: IonosDns, args: argparse.Namespace) -> None:
    if not args.apply:
        raise SystemExit("Refusing to apply without --apply. Use snapshot/diff first.")
    results = []
    for desired in load_desired_records(args.map_file):
        ns = argparse.Namespace(
            name=desired["name"],
            type=desired.get("type", "A"),
            content=desired["content"],
            ttl=int(desired.get("ttl", args.ttl)),
            prio=desired.get("prio"),
            allow_multiple=False,
        )
        zone, records = client.records(ns.name)
        zone_id = str(zone.get("id") or "")
        record = {
            "name": ns.name.rstrip("."),
            "type": ns.type.upper(),
            "content": ns.content,
            "ttl": int(ns.ttl),
            "disabled": False,
        }
        matches = find_matching_records(records, ns.name, ns.type)
        if matches:
            result = client.patch_record(zone_id, str(matches[0].get("id")), record)
            status = "updated"
        else:
            result = client.create_record(zone_id, record)
            status = "created"
        results.append({"name": ns.name, "type": ns.type, "status": status, "result": result})
    print(json.dumps(results, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key-file", default="", help="Path containing the IONOS X-API-Key value.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list-zones")
    p.set_defaults(func=command_list_zones)

    p = sub.add_parser("snapshot")
    p.add_argument("--scope", required=True)
    p.set_defaults(func=command_snapshot)

    p = sub.add_parser("ensure-record")
    p.add_argument("--name", required=True)
    p.add_argument("--type", required=True)
    p.add_argument("--content", required=True)
    p.add_argument("--ttl", type=int, default=300)
    p.add_argument("--prio", type=int)
    p.add_argument("--allow-multiple", action="store_true")
    p.set_defaults(func=command_ensure_record)

    p = sub.add_parser("ensure-txt")
    p.add_argument("--name", required=True)
    p.add_argument("--content", required=True)
    p.add_argument("--ttl", type=int, default=60)
    p.add_argument("--prio", type=int)
    p.add_argument("--propagation-seconds", type=int, default=60)
    p.set_defaults(func=command_ensure_txt)

    p = sub.add_parser("delete-txt")
    p.add_argument("--name", required=True)
    p.add_argument("--content", required=True)
    p.set_defaults(func=command_delete_txt)

    p = sub.add_parser("apply-map")
    p.add_argument("--map-file", required=True)
    p.add_argument("--ttl", type=int, default=300)
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=command_apply_map)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    client = IonosDns(read_api_key(args))
    try:
        args.func(client, args)
    except IonosError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

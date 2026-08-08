from services.inventory_model import build_actionable_inventory
from services.rules_store import load_rules


def main() -> None:
    rules = load_rules()
    for group in build_actionable_inventory(rules):
        print(f"[{group['name']}]")
        for host in group.get("hosts", []):
            route_target = host.get("route_target")
            route = f" -> {route_target}" if route_target else ""
            status = "ready" if host.get("ready") else host.get("reason")
            print(f"  {host['name']}{route}: {status}")


if __name__ == "__main__":
    main()

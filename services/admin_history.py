import json
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
ADMIN_HISTORY_PATH = BASE_DIR / "dictionaries" / "admin_history.json"


def load_admin_history() -> list[dict]:
    if not ADMIN_HISTORY_PATH.exists():
        return []
    with ADMIN_HISTORY_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def append_admin_history(entry: dict) -> None:
    history = load_admin_history()
    history.insert(0, {"timestamp": datetime.now(timezone.utc).isoformat(), **entry})
    with ADMIN_HISTORY_PATH.open("w", encoding="utf-8") as handle:
        json.dump(history[:50], handle, indent=2)
        handle.write("\n")

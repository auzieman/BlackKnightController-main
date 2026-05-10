"""API key scope parsing and authorization for /api/v1."""

from __future__ import annotations

DEFAULT_SCOPES = ("read:me", "read:inventory")

# Flask endpoint name -> required scope (Bearer routes only)
ENDPOINT_REQUIRED_SCOPE: dict[str, str] = {
    "api_v1.me": "read:me",
    "api_v1.inventory": "read:inventory",
    "api_v1.automation_runs": "read:automation",
    "api_v1.automation_run_detail": "read:automation",
    "api_v1.automation_trigger": "write:automation",
}


def parse_scopes(raw: str | None) -> set[str]:
    if not raw or not str(raw).strip():
        return set(DEFAULT_SCOPES)
    parts = {p.strip().lower() for p in str(raw).split(",") if p.strip()}
    return parts or set(DEFAULT_SCOPES)


def scope_allowed(granted: set[str], required: str) -> bool:
    if "*" in granted or "all" in granted:
        return True
    return required.lower() in granted

def _normalize(value: str) -> str:
    return " ".join(value.lower().replace("_", " ").replace("-", " ").split())


def parse_workflow_stages(workflow: str | None) -> list[str]:
    raw = workflow or "build -> provision -> configure -> deploy"
    if "->" in raw:
        parts = [part.strip() for part in raw.split("->")]
    else:
        parts = [part.strip() for part in raw.split(",")]
    stages = [part for part in parts if part]
    return stages or ["build", "provision", "configure", "deploy"]


def build_stage_columns(hosts: list[tuple[str, dict]], workflow: str | None) -> list[dict]:
    stages = parse_workflow_stages(workflow)
    columns = [{"name": stage, "hosts": []} for stage in stages]
    unmatched = []

    for host_name, host_data in hosts:
        state = _normalize(str(host_data.get("state", "") or ""))
        matched = False
        for column in columns:
            if state == _normalize(column["name"]):
                column["hosts"].append({"name": host_name, "data": host_data})
                matched = True
                break
        if not matched:
            unmatched.append({"name": host_name, "data": host_data})

    if unmatched:
        columns.append({"name": "Unassigned", "hosts": unmatched})

    return columns

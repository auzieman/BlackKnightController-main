from services.automation_runs import load_runs


def main() -> None:
    runs = sorted(
        load_runs(),
        key=lambda run: run.get("updated_at") or run.get("created_at") or "",
        reverse=True,
    )
    for run in runs[:40]:
        extra = run.get("extra") or {}
        pipeline_id = extra.get("pipeline_id") or run.get("workflow") or ""
        text = " ".join(
            [
                str(pipeline_id),
                str(run.get("workflow") or ""),
                str(run.get("notes") or ""),
                str(extra),
            ]
        ).lower()
        if not any(token in text for token in ("auzix", "package", "desktop", "flatpak", "podman")):
            continue
        print(run.get("id"), run.get("status"), pipeline_id, run.get("updated_at") or run.get("created_at"))
        notes = str(run.get("notes") or "")
        if notes:
            print("  notes=", notes[:220])


if __name__ == "__main__":
    main()

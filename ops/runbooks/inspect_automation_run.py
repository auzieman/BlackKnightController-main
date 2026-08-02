"""Print a compact JSON summary of a BKC automation run from inside a container."""

from __future__ import annotations

import json
import os
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: inspect_automation_run.py RUN_ID")

    os.environ["BKC_TENANT_SLUG"] = os.environ.get("BKC_TENANT_SLUG", "lab")

    import bkc_server
    from services.automation_runs import get_run

    with bkc_server.app.app_context():
        run = get_run(sys.argv[1])
        if not run:
            print(json.dumps({"missing": True, "run_id": sys.argv[1]}, sort_keys=True))
            return

        print(
            json.dumps(
                {
                    "id": run.get("id"),
                    "status": run.get("status"),
                    "workflow": run.get("workflow"),
                    "stages": [
                        {
                            "name": stage.get("name"),
                            "status": stage.get("status"),
                            "detail": stage.get("detail"),
                        }
                        for stage in run.get("stages", [])
                    ],
                    "events_tail": run.get("events", [])[-8:],
                    "extra": run.get("extra", {}),
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()

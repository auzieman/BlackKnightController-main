# BKC session baseline — 2026-08-24

BKC already contained the lab topology, power route, SSH aliases, Docker
contexts, pipeline ownership, and anti-spiral guidance. The recurring failure
was not missing documentation. Sessions could mutate infrastructure without
first loading that authority, and successful one-offs were not consistently
reconciled into their governing pipelines.

Start by declaring intent:

```sh
./ops/bkc-session-bootstrap.sh lab-bringup
./ops/bkc-session-bootstrap.sh auzix-build
./ops/bkc-session-bootstrap.sh auzix-vm-validation
```

The output identifies the control-plane master, host roles, repository state,
and governing pipeline. A direct mutation is an exception, not an alternate
workflow:

```sh
BKC_SESSION_EXCEPTION=issue-or-run-id ./ops/bkc-session-bootstrap.sh auzix-build
```

The exception remains unfinished until its change and regression proof are
folded into the named pipeline. Read-only diagnostics do not require an
exception.

The next implementation step is to make BKC record this bootstrap data in each
run receipt and reject mutation actions that have neither a pipeline context
nor an exception receipt.

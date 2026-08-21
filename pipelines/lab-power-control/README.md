# LAB 01 — Power Control

Thin BKC-visible wrapper around `ops/lab-power.sh`.

This is the operator-facing lane for the boring-but-critical lab lifecycle:

```bash
ops/lab-power.sh status
ops/lab-power.sh start
ops/lab-power.sh wait
ops/lab-power.sh park
ops/lab-power.sh check-autostart
```

Keep the implementation in `ops/lab-power.sh`; keep this pipeline as the UI/API
handle so the action leaves a receipt trail. The script runs through ns1 for
IPMI, not from random workstation shell experiments.


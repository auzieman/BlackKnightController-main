# Windows 10 PXE Smoke Dictionary

Runtime values for `pipelines/windows10-pxe-smoke`.

VMID 136 is intentionally disposable. VMID 113 remains a reference target.

The active smoke profile targets Windows 10 Pro, uses the generic Pro KMS
client setup key for unattended setup, and creates one whole-disk `C:`
partition. Post-install package personalization is deferred until SSH control
is available from BKC.

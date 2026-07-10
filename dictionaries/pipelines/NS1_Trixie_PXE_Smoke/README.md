# NS1 Trixie PXE Smoke Dictionary

Runtime values for `pipelines/ns1-trixie-pxe-smoke`.

The active smoke profile recreates disposable VMID 132, boots it once through
PXE, and flips the VM back to disk-first order. Blank password and SSH key
fields are filled at render time by BKC; generated credentials are staged on
ns1 under `/root`.

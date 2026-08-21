# AUZiX 24 — Stage ISO + Reload Test VM

Stage the current AUZiX ISO to the selected lab hypervisor and reload a
disposable test VM so the operator can review the live ISO.

This is intentionally separate from package building and disk install. Its job
is only:

1. verify the ISO/checksum exists;
2. stage it to the hypervisor/datastore;
3. attach it to the test VM;
4. hard power-cycle the VM into live ISO review;
5. record the console URL, VM identity, and expected first checks.


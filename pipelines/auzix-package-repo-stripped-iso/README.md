# AUZiX package repo deploy + stripped ISO proof

This candidate pipeline turns the package factory into a visible BKC lane:

1. validate AUZiX package intent JSON,
2. build the AUZiX package repository from strict-root receipts,
3. optionally publish that repository to the served lab path,
4. audit the root with classic Unix top-level directories treated as invalid,
5. feed only receipts and reports to the Ollama worker,
6. optionally rebuild and validate a stripped install ISO,
7. optionally prove package install behavior on VM135.

The rule is simple: `/System`, `/Programs`, `/Services`, `/Stacks`, `/Work`,
`/Users`, `/Volumes`, and `/Network` are the AUZiX contract. Classic top-level
paths such as `/usr`, `/lib`, `/bin`, `/etc`, `/var`, `/tmp`, `/home`, and
`/root` are not allowed back into the base identity. If one returns, it must be
tracked as break-fix compatibility debt with an owner and removal condition.

## Safe first run

From the AUZiX repo on the build host or worker:

```sh
python3 -m json.tool packages/extended-ports.manifest.json >/dev/null
python3 -m json.tool packages/oci-and-python.queue.json >/dev/null
python3 -m json.tool packages/flatpak-desktop.queue.json >/dev/null

./scripts/build-auzix-package-repo.sh out/auzix-strict/AuzixRoot

AUZIX_LEGACY_POLICY=invalid \
  ./scripts/audit-auzix-strict-root.sh \
  out/auzix-strict/AuzixRoot \
  out/package-repo-stripped-iso/strict-root-audit.txt
```

That first run is expected to fail while the current staged root still contains
compatibility links. A useful proof is not green yet; a useful proof is loud,
specific, and repeatable.

## Gated operations

These are disabled in `defaults.json` until the operator flips them:

- `enable_package_build`: run package-bot batches for base/OCI/Flatpak packages.
- `enable_package_publish`: publish the generated repository to `/srv/http/auzix/repo`.
- `enable_stripped_iso_build`: rebuild the install ISO without classic path links.
- `enable_vm135_install_validation`: install from the repo inside VM135.
- `enable_bkc_channel_post`: post progress to bkc-channel.

## Ollama worker role

Ollama is in the loop as a receipt reviewer, not as an authority to mutate the
lab. It receives package build reports, repository validation reports,
strict-root audit reports, and ISO validation receipts. Its job is to propose
the smallest next fix when a package dependency, runtime link, or root-contract
check fails.

Never send secrets, raw environment dumps, private cert material, SSH keys, or
API tokens to the model.


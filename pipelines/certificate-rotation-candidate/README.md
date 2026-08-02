# Certificate Rotation Candidate

Candidate pipeline for discovering, renewing, staging, swapping, validating,
and rolling back TLS certificates.

Start with `lab.auzietek.com`, then promote the pattern to
`beta.auzietek.com`, then production `auzietek.com`.

Private keys and provider secrets must remain runtime-only.


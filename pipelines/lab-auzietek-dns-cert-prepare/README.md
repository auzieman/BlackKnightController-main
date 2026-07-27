# lab.auzietek.com DNS + Let's Encrypt Prepare

Candidate pipeline for turning `lab.auzietek.com` into the safe named lab edge.

This is intentionally split into gated steps:

1. install helpers on ns1
2. snapshot current IONOS DNS state
3. optionally apply the lab DNS map
4. optionally request the Let's Encrypt wildcard cert
5. optionally spool certs to service targets
6. validate DNS/TLS

The IONOS key is always a secret reference and must not be committed.

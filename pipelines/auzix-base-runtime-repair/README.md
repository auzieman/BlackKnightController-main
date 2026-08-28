# AUZiX targeted base-runtime repair

This lane repairs a missing negotiated base provider without replaying the
1,133-package workstation intake. It is intentionally limited to the reviewed
Trixie providers for `GCC14Base` and `LibgccS1`.

The pipeline builds those two packages with dependency discovery disabled,
writes a separate immutable repair spool, verifies that `LibgccS1` owns
`libgcc_s.so.1`, and merges the spool over the frozen consolidated repository
as a new release. The source release is never modified in place.

The final gate extracts the repaired base providers from the new repository
into a clean proof root and requires the canonical AUZiX libgcc payload before
container validation may resume.

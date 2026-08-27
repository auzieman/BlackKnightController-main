# AUZiX release container validation guardrails

- Reload AUZiX and BKC session policies before interpreting artifacts.
- The repository catalog is not an install manifest.
- Resolve one explicit, finite validation closure before materialization.
- Missing dependencies are repository-stage failures; never discover or fetch them here.
- Install through preserved package payloads and lifecycle hooks in dependency order.
- `/System/Libraries/Runtime/glibc` is the only core glibc provider.
- Python, ncurses/terminfo, Glances, htop, and LibreOffice headless conversion are required first-boot probes.
- A package named as a release/runtime proof must come from a current reviewed
  spool. It may not be silently selected from the legacy `safe_reuse` set.
- A completed bulk repack count does not prove that named leaf applications or
  their split runtimes were included; inspect spool entries by package identity
  before consolidation.
- Harvest package-provided desktop metadata; unresolved launcher commands fail validation.
- No HDD/image pipeline may run without this pipeline's passing receipt.

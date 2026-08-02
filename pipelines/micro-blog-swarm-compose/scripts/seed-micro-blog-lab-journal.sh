#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  seed-micro-blog-lab-journal.sh TARGET_CONTENT_DIR SOURCE_MD...

Example:
  seed-micro-blog-lab-journal.sh /srv/micro-blog/content/posts/blackknight docs/video-wrap/*.md pipelines/*/README.md

Copies selected markdown notes into a dated micro-blog content folder without
modifying the source files. This is intentionally simple: the app's filesystem
import/bootstrap path owns conversion into blog records.
USAGE
}

if [[ $# -lt 2 ]]; then
  usage
  exit 2
fi

target_root="$1"
shift

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target_dir="${target_root%/}/blackknight-lab-${stamp}"
mkdir -p "${target_dir}"

copied=0
for source in "$@"; do
  if [[ ! -f "${source}" ]]; then
    echo "Skipping non-file: ${source}" >&2
    continue
  fi
  case "${source}" in
    *.md|*.markdown)
      base="$(basename "${source}")"
      cp "${source}" "${target_dir}/${base}"
      copied=$((copied + 1))
      ;;
    *)
      echo "Skipping non-markdown file: ${source}" >&2
      ;;
  esac
done

cat > "${target_dir}/_blackknight-lab-index.md" <<MD
# BlackKnight Lab Journal ${stamp}

This folder was staged by the BlackKnightController micro-blog swarm candidate.

It is meant as a lightweight narrative seed: deployment evidence, known-good
fragments, and short runbook notes can be imported into micro-blog so the lab can
tell its own story.

Copied markdown files: ${copied}
MD

echo "Seeded ${copied} markdown files into ${target_dir}"

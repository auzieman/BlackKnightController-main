#!/usr/bin/env bash
set -euo pipefail

apply=false
reset_app_caches=false

usage() {
  cat <<'USAGE'
Fedora media stack repair helper for Shotcut/OpenShot/GStreamer/FFmpeg weirdness.

Default mode is diagnostic only.

Usage:
  tools/fedora_media_stack_repair.sh
  tools/fedora_media_stack_repair.sh --apply
  tools/fedora_media_stack_repair.sh --reset-app-caches
  tools/fedora_media_stack_repair.sh --apply --reset-app-caches

What --apply does:
  - refreshes dnf metadata
  - syncs installed packages to enabled repos
  - swaps Fedora ffmpeg-free to RPM Fusion ffmpeg when possible
  - installs common RPM Fusion multimedia/GStreamer codec packages
  - refreshes GStreamer registry cache

What --reset-app-caches does:
  - moves Shotcut/OpenShot user cache/config dirs to a timestamped backup
  - does not delete project files or media

Notes:
  - Requires RPM Fusion free/nonfree repos to be enabled for full codec support.
  - Does not remove user video projects.
  - Does not touch ~/.ssh, browser profiles, or system boot settings.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply)
      apply=true
      ;;
    --reset-app-caches)
      reset_app_caches=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

run() {
  echo
  echo "+ $*"
  if "$apply"; then
    "$@"
  else
    echo "  dry-run: skipped"
  fi
}

section() {
  echo
  echo "== $* =="
}

section "OS"
cat /etc/os-release | sed -n '1,12p'

section "Enabled repos"
dnf repolist --enabled | sed -n '1,160p'

section "Installed media packages"
rpm -qa | grep -Ei 'ffmpeg|gstreamer|openh264|shotcut|openshot|mlt|pipewire|mesa' | sort | sed -n '1,260p' || true

section "Available RPM Fusion multimedia candidates"
dnf repoquery --available \
  'ffmpeg' \
  'ffmpeg-libs' \
  'ffmpeg-free' \
  'libavcodec-freeworld' \
  'gstreamer1-plugins-bad-freeworld' \
  'gstreamer1-plugins-ugly' \
  'gstreamer1-plugin-libav' \
  'x264' \
  'x265-libs' 2>/dev/null | sort -u | sed -n '1,220p' || true

section "Binary probes"
for tool in ffmpeg ffprobe gst-inspect-1.0 shotcut openshot-qt; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "$tool -> $(command -v "$tool")"
    "$tool" --version 2>/dev/null | sed -n '1,3p' || true
  else
    echo "$tool -> missing"
  fi
done

section "GStreamer plugin probes"
if command -v gst-inspect-1.0 >/dev/null 2>&1; then
  for plugin in libav openh264 x264 x265 vaapi pipewire; do
    if gst-inspect-1.0 "$plugin" >/dev/null 2>&1; then
      echo "gst plugin $plugin -> ok"
    else
      echo "gst plugin $plugin -> missing or not loadable"
    fi
  done
else
  echo "gst-inspect-1.0 missing; install gstreamer1-tools if deeper GStreamer probing is needed."
fi

if "$apply"; then
  section "Applying package repair"
  run sudo dnf makecache --refresh
  run sudo dnf distro-sync --refresh --allowerasing
  run sudo dnf swap ffmpeg-free ffmpeg --allowerasing
  run sudo dnf install -y \
    ffmpeg \
    ffmpeg-libs \
    compat-ffmpeg4 \
    libavcodec-freeworld \
    gstreamer1-plugin-libav \
    gstreamer1-plugins-bad-freeworld \
    gstreamer1-plugins-ugly \
    gstreamer1-plugins-good \
    gstreamer1-plugins-good-gtk \
    gstreamer1-plugins-good-qt \
    gstreamer1-plugins-good-qt6 \
    gstreamer1-plugins-bad-free-extras \
    gstreamer1-plugins-ugly-free \
    gstreamer1-plugin-openh264 \
    openh264 \
    mozilla-openh264 \
    mlt \
    mlt-qt5 \
    mlt-qt6 \
    shotcut \
    openshot \
    python3-libopenshot \
    libopenshot \
    libopenshot-audio
  run sudo dnf autoremove
  run sudo ldconfig
fi

if "$reset_app_caches"; then
  section "Moving Shotcut/OpenShot caches/configs aside"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup_dir="${HOME}/.local/state/media-stack-cache-backups/${stamp}"
  mkdir -p "$backup_dir"
  candidates=(
    "${HOME}/.cache/OpenShot"
    "${HOME}/.cache/openshot-qt"
    "${HOME}/.config/OpenShot"
    "${HOME}/.config/openshot_qt"
    "${HOME}/.local/share/openshot_qt"
    "${HOME}/.cache/Meltytech"
    "${HOME}/.cache/Shotcut"
    "${HOME}/.config/Meltytech"
    "${HOME}/.config/Shotcut"
    "${HOME}/.local/share/Meltytech"
    "${HOME}/.local/share/Shotcut"
  )
  for path in "${candidates[@]}"; do
    if [ -e "$path" ]; then
      echo "moving $path -> $backup_dir/"
      mv "$path" "$backup_dir/"
    fi
  done
  echo "cache/config backup: $backup_dir"
fi

section "GStreamer registry refresh"
if command -v gst-inspect-1.0 >/dev/null 2>&1; then
  registry="${HOME}/.cache/gstreamer-1.0/registry.x86_64.bin"
  if [ -e "$registry" ]; then
    if "$apply"; then
      mv "$registry" "${registry}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
      echo "moved old registry cache aside"
    else
      echo "dry-run: would move $registry aside"
    fi
  fi
  if "$apply"; then
    gst-inspect-1.0 >/dev/null || true
  fi
fi

section "Next manual smoke checks"
cat <<'CHECKS'
Try:
  ffmpeg -hide_banner -codecs | grep -Ei '264|265|aac|opus|vorbis' | head
  gst-inspect-1.0 libav
  shotcut
  openshot-qt

If Shotcut still cannot export:
  - try an H.264/AAC MP4 preset after the ffmpeg swap
  - try VP9/Opus WebM as a fallback
  - check whether the source clip itself has a missing/truncated moov atom

If OpenShot still crashes on mouse move/preview:
  - that AttributeError looked like an OpenShot UI bug path
  - use Shotcut/Kdenlive/Pitivi for the immediate video and revisit OpenShot later
CHECKS

if ! "$apply"; then
  echo
  echo "Dry-run complete. Re-run with --apply to repair packages."
fi

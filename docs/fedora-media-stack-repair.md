# Fedora media stack repair helper

Status: workstation helper

Use this when Fedora updates leave Shotcut/OpenShot/GStreamer/FFmpeg in a weird
state where preview or export fails.

Diagnostic only:

```sh
tools/fedora_media_stack_repair.sh
```

Apply package repair:

```sh
tools/fedora_media_stack_repair.sh --apply
```

Move Shotcut/OpenShot caches/config aside, without deleting projects/media:

```sh
tools/fedora_media_stack_repair.sh --reset-app-caches
```

Full repair attempt:

```sh
tools/fedora_media_stack_repair.sh --apply --reset-app-caches
```

The helper prefers the RPM Fusion multimedia stack on Fedora, including full
`ffmpeg`, `libavcodec-freeworld`, and the GStreamer bad/ugly/freeworld plugins.

It does not touch video project folders, source clips, SSH keys, browser
profiles, or boot settings.

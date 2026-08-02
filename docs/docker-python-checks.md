# Docker-backed Python checks

Status: local developer helper

Use Docker for Python import/smoke checks when the workstation environment is
not expected to have BKC dependencies installed.

Default beta graph smoke:

```sh
tools/docker_python_check.sh
```

Run an ad-hoc Python snippet inside the same disposable environment:

```sh
tools/docker_python_check.sh 'from routes.beta_ui import _fabric_cards; print(len(_fabric_cards()))'
```

This keeps the laptop Python environment out of the critical path. The
container installs `requirements.txt` into a throwaway filesystem and mounts the
repo read-only.

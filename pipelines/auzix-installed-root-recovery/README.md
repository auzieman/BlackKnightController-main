# AuziX Installed Root Recovery

Purpose: keep the installed-root permission and browser/network recovery path
repeatable through BKC instead of direct manual VM work.

This scaffold records the intended lane before it is wired into the executor.
It should stay narrow:

- verify the expected AuziX source commit
- verify the build workspace and target VM have enough disk
- build or reuse the strict desktop payload
- deploy only through the BKC lane
- run the installed-root finalizer
- validate `/Users/auzix`, `/run/user/1000`, `/dev/shm`, sudo, Xorg wrapper,
  browser state, and basic network access

The first implementation should prefer checks that already exist in AuziX, then
move any long command blocks into `checks/` or `templates/`.

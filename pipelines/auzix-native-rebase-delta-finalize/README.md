# AUZiX locked delta and native finalize

This pipeline resumes a preserved native-rebase root without replaying its
completed package transaction. It checks out an exact AUZiX tag in the existing
run source, installs only the committed delta with dependency discovery off,
and then runs the native Flatpak, desktop, repository, and validation stages.

If a native stage requests a package absent from the lock, execution stops. Add
the package to discovery, review a new lock, and rerun a new delta; never allow
the post-stage to discover dependencies dynamically.

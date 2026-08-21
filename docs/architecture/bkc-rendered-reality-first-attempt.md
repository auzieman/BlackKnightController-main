# BKC rendered reality: first attempt notes

These notes capture the mental model that emerged while hardening the AUZiX
package/ISO pipeline work.

## The core idea

BKC should not merely execute scripts. BKC should render artifacts from intent
and then realize them on targets.

Everything can be treated as a template when the inputs and realization rules
are controlled:

- shell scripts;
- Python scripts;
- Lua scripts;
- C source;
- package metadata;
- package pre/post hooks;
- system service units;
- desktop entries;
- installer pages;
- ISO profiles;
- pipeline fragments;
- validation probes.

The final execution path differs by type, but the lifecycle is common:

```text
declare -> render -> validate -> stage -> realize -> verify -> record
```

## Why this is hard to see

Humans and AIs both tend to treat interpreted scripts as monoliths. A shell
script feels like "the thing that runs." In BKC it should usually be "the thing
that was rendered for this target and this intent."

That is the same lesson hiding in Puppet, Chef, Ansible, and web frameworks:
the controller/server side transforms data and templates into concrete target
artifacts. The target should receive the simplest possible finished file.

## Why self-control matters

This looks open-ended because it is powerful enough to render nearly anything.
The control mechanism is a strict contract:

1. JSON declares intent.
2. Facts describe the target.
3. Templates transform intent and facts into concrete artifacts.
4. Executors realize artifacts according to type.
5. Pipelines record the exact rendered artifact, checksum, stdout/stderr,
   status, and facts.

No magical mutation. No invisible hand fixes. No unrecorded shell improvisation
as the final state.

## AUZiX implication

AUZiX standalone can expose normal scripts and samples for users who are not
running BKC.

BKC-mode AUZiX should prefer generated artifacts:

```text
package intent
  -> rendered build script / hook / desktop file / validator / receipt
  -> staged target root or builder
  -> chmod/chown/compile/run
  -> facts and receipts back into BKC
```

The existing `scripts/test-auzix-package-bot.sh` pattern should become a
rendered package-bot contract in BKC:

```text
defaults.package-bot-contract.json
  + test-auzix-package-bot.sh.j2
  -> /tmp/bkc-runs/<run_id>/test-auzix-package-bot.sh
  -> run inside the checked-out AUZiX root
  -> capture result
```

## Package-tool adapter implication

A universal package installer is not one giant script. It is a rendered plan:

```text
detect OS/package tool
  -> choose apt/dpkg, dnf/rpm, apk, pacman, auzix-pkg, ...
  -> for package in desired packages
  -> render native query/install/remove/validate operations
  -> execute and report facts
```

The same desired package set can become apt commands on Debian, apk commands on
Alpine, dnf commands on Fedora, or auzix-pkg operations on AUZiX.

## Codex guardrail

When tempted to write or run a custom shell command, ask:

1. Is this just a one-time read-only inspection?
2. If not, what is the intent JSON?
3. What facts should select behavior?
4. What artifact should be rendered?
5. Where should BKC store the rendered artifact and checksum?
6. What executor should realize it?
7. What facts should come back?

If the answer exists, render through BKC. Do not keep extending the loose-cannon
shell habit.

The planet is dynamic. BKC should act like it knows that.

# BKC intent adapters and package operations

BKC should treat package work as an intent-to-operation problem, not as a
hand-authored shell blob.

The core pattern is:

```text
target facts + desired package intent
        -> detect OS and package tooling
        -> select the native verb set
        -> render an ordered operation plan
        -> execute with BKC stage/event logging
        -> collect resulting package facts
```

This lets BKC do the useful part of Ansible with less ceremony: data in,
deterministic rendered actions out, every step visible in the pipeline UI.

## Package tool adapter contract

A package adapter is selected from detected target facts:

```json
{
  "os": {
    "id": "debian",
    "version_codename": "trixie"
  },
  "package_tool": {
    "family": "apt",
    "query": "dpkg-query",
    "install": "apt-get",
    "remove": "apt-get",
    "status": "dpkg-query"
  }
}
```

The same package intent can then compile to different verbs:

| Family | Query installed | Install | Remove |
| --- | --- | --- | --- |
| apt/dpkg | `dpkg-query -W` | `apt-get install` | `apt-get remove` |
| dnf/rpm | `rpm -q` | `dnf install` | `dnf remove` |
| apk | `apk info -e` | `apk add` | `apk del` |
| pacman | `pacman -Q` | `pacman -S` | `pacman -R` |
| auzix-pkg | `auzix-pkg status` | `auzix-pkg install` | `auzix-pkg remove` |

AUZiX uses the same model, but with AUZiX paths, receipts, dependency locks,
and maintainer hook policy.

## Rendered package operation loop

The pipeline should not contain one stage per package by hand. It should contain
one package intent and let BKC render the loop:

```jinja
{% for pkg in packages %}
- id: package-{{ loop.index }}-{{ pkg.name }}-status
  action: package.status
  with:
    package: {{ pkg.name | tojson }}

- id: package-{{ loop.index }}-{{ pkg.name }}-install
  action: package.install
  enabled_when: "{{ '{{' }} not facts.packages[{{ pkg.name | tojson }}].installed {{ '}}' }}"
  with:
    package: {{ pkg.name | tojson }}
    version: {{ pkg.version | default("") | tojson }}

- id: package-{{ loop.index }}-{{ pkg.name }}-validate
  action: package.validate
  with:
    package: {{ pkg.name | tojson }}
    checks: {{ pkg.checks | default([]) | tojson }}
{% endfor %}
```

BKC should persist the rendered operation plan as a run artifact before
execution. That gives the operator, Astra, Ollama, and Codex the exact thing
that will run.

## Server-side render, target-side execution

BKC should not push raw intent and hope the target shell interprets it
correctly. The controller should render the final target-ready artifact first,
then deliver that exact artifact.

This is the Puppet/Ansible/Chef lesson in BKC terms:

```text
JSON intent + Jinja template + target facts
        -> controller-side render
        -> artifact checksum and run attachment
        -> upload to target staging path
        -> chmod/chown if required
        -> execute or place atomically
        -> collect stdout/stderr/status/facts
        -> optional cleanup
```

The target receives a concrete file, not a half-evaluated plan full of inline
substitution and fragile quoting.

Example rendered package runner:

```jinja
#!/usr/bin/env sh
set -eu

{% if adapter.family == "apt" %}
export DEBIAN_FRONTEND=noninteractive
{% endif %}

{% for pkg in packages %}
echo "BKC_PACKAGE_BEGIN {{ pkg.name }}"
{% if adapter.family == "apt" %}
if ! dpkg-query -W -f='${Status}' {{ pkg.native_name | shquote }} 2>/dev/null | grep -q 'install ok installed'; then
  apt-get install -y {{ pkg.native_name | shquote }}
fi
{% elif adapter.family == "apk" %}
if ! apk info -e {{ pkg.native_name | shquote }} >/dev/null 2>&1; then
  apk add {{ pkg.native_name | shquote }}
fi
{% elif adapter.family == "auzix-pkg" %}
if ! auzix-pkg status {{ pkg.name | shquote }} >/dev/null 2>&1; then
  auzix-pkg install {{ pkg.name | shquote }}
fi
{% endif %}
echo "BKC_PACKAGE_END {{ pkg.name }}"
{% endfor %}
```

The Jinja template is generic. The JSON selects the adapter, packages, target
paths, and validation rules. The rendered shell is disposable and auditable.

## Generic execution primitive

BKC needs a reusable primitive with this shape:

```json
{
  "action": "artifact.render_stage_execute",
  "with": {
    "template": "templates/package-install.sh.j2",
    "intent": "${dictionary.package_install_intent}",
    "target": "${dictionary.target_host}",
    "remote_path": "/tmp/bkc-runs/${run_id}/package-install.sh",
    "mode": "0755",
    "execute": true,
    "cleanup": true
  }
}
```

The executor should:

1. merge pipeline inputs, dictionary data, target facts, and run metadata;
2. render Jinja on the BKC side;
3. store the rendered artifact and checksum in the run record;
4. upload the artifact to the target;
5. apply requested mode/owner/group;
6. execute it with bounded timeout when requested;
7. collect stdout, stderr, exit code, and any declared output files;
8. optionally remove the staged artifact.

This avoids most shell quoting gremlins because the complex logic is compiled
before the target ever sees it.

## AUZiX-specific use

For AUZiX package installation, the same adapter needs a stricter contract:

1. load the selected package profile or installer choices;
2. resolve against the locked AUZiX package index;
3. dedupe packages already present in `/System/PackageDB/installed.json`;
4. install dependencies in declared build/install order;
5. extract payloads preserving owner, group, mode, setuid, sticky bits, and
   symlink intent;
6. run package pre/post hooks under the target root;
7. update and reload the central AUZiX installed package DB after each package;
8. run validation checks from the target root namespace;
9. emit package facts back to BKC.

The important rule: troubleshooting can happen, but the outcome must become an
adapter rule, package contract, or pipeline template before the next run.

## Why this matters

Raw remote shell makes Codex improvise. Intent adapters make BKC compile.

That is the correct center of gravity:

- BKC owns target discovery and operation rendering.
- Package tools own native package semantics.
- AUZiX owns path relocation, package receipts, and lifecycle hooks.
- Pipelines own visibility, ordering, logs, artifacts, and retry boundaries.

This is how AUZiX becomes reproducible instead of heroic.

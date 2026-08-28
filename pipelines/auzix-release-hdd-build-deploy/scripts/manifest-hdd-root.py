#!/usr/bin/env python3
"""Emit a deterministic content/mode manifest for an AUZiX root."""

import argparse
import hashlib
import os
import stat
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("root", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
root = args.root.resolve()
digest = hashlib.sha256()
with args.output.open("w") as stream:
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root))):
        relative = str(path.relative_to(root))
        mode = path.lstat().st_mode
        if stat.S_ISREG(mode):
            value = hashlib.sha256(path.read_bytes()).hexdigest()
            kind = "file"
        elif stat.S_ISLNK(mode):
            value = os.readlink(path)
            kind = "link"
        elif stat.S_ISDIR(mode):
            value = "-"
            kind = "dir"
        else:
            value = "-"
            kind = "other"
        record = f"{kind}\t{mode & 0o7777:04o}\t{value}\t{relative}\n"
        stream.write(record)
        digest.update(record.encode())
print(digest.hexdigest())

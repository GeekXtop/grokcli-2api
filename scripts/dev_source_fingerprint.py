#!/usr/bin/env python3
"""Compute a deterministic digest for the Go API source inputs."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys


PATTERNS = ("cmd/**/*.go", "internal/**/*.go", "go.mod", "go.sum", ".release-commit")


def source_files(root: Path) -> list[Path]:
    """Return existing source inputs as sorted paths relative to ``root``."""

    found = {
        path.relative_to(root)
        for pattern in PATTERNS
        for path in root.glob(pattern)
        if path.is_file()
    }
    return sorted(found, key=lambda path: path.as_posix())


def source_digest(root: Path) -> str:
    """Hash each relative path and its bytes with NUL separators."""

    digest = sha256()
    for relative in source_files(root):
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: dev_source_fingerprint.py ROOT", file=sys.stderr)
        return 2

    root = Path(args[0]).resolve()
    if not root.is_dir():
        print(f"error: ROOT is not a directory: {root}", file=sys.stderr)
        return 2

    try:
        value = source_digest(root)
    except OSError as exc:
        print(f"error: cannot fingerprint {root}: {exc}", file=sys.stderr)
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

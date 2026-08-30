#!/usr/bin/env python3
"""Regenerate the checked-in institutional-memory build artifact."""
from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

if __package__:
    from . import prompt_assembler
else:
    import prompt_assembler  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_PATH = REPO_ROOT / "knowledge" / "institutional-memory.md"


def _atomic_text_write(path: Path, content: str) -> None:
    """Replace ``path`` with ``content`` without exposing a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def regenerate_institutional_memory(
    output_path: Path | None = None,
    *,
    check_only: bool = False,
) -> int:
    """Check or atomically regenerate the rendered institutional-memory file."""
    path = output_path if output_path is not None else DEFAULT_OUTPUT_PATH
    rendered = prompt_assembler.render_institutional_memory()

    if check_only:
        try:
            return 0 if path.read_text(encoding="utf-8") == rendered else 1
        except FileNotFoundError:
            return 1

    _atomic_text_write(path, rendered)
    return 0


def main() -> int:
    """Parse CLI arguments and return the requested operation's status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when output is stale or missing")
    parser.add_argument("--output", type=Path, help="path to regenerate or check")
    arguments = parser.parse_args()
    return regenerate_institutional_memory(arguments.output, check_only=arguments.check)


if __name__ == "__main__":
    raise SystemExit(main())

"""Load a .env file into os.environ. Standard library only."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path | str | None = None) -> None:
    """Read KEY=VALUE lines from a .env file into os.environ.

    Existing environment variables take precedence (so you can still
    override with a real export). Lines starting with # are comments.
    """
    if path is None:
        path = Path(__file__).resolve().parent / ".env"
    else:
        path = Path(path)

    if not path.is_file():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip("\"'")
        if key not in os.environ:
            os.environ[key] = value

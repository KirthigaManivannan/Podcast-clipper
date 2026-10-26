"""Check that OPENROUTER_API_KEY is present and non-placeholder in .env.

Used by start.bat / start.sh so they can stop early with a friendly message
before installing anything. Uses only the standard library so it runs with any
Python. Exit code 0 = OK, 1 = missing/placeholder.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def load_key(env_path: Path) -> str | None:
    """Return the OPENROUTER_API_KEY value from a .env file, or None."""
    try:
        text = env_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    for line in text.splitlines():
        if re.match(r"^\s*OPENROUTER_API_KEY\s*=", line):
            value = line.split("=", 1)[1].strip()
            return value.strip('"').strip("'")
    return None


def is_placeholder(key: str) -> bool:
    lowered = key.lower()
    return (
        not key
        or lowered.startswith("your_")
        or lowered in {"changeme", "placeholder", "xxx", "yourkey"}
        or key.startswith("<")
    )


def main() -> int:
    env = Path(".env")
    key = load_key(env)
    if key is None or is_placeholder(key):
        print(
            "\n[podclip] OPENROUTER_API_KEY is missing or still the placeholder.\n"
            f"Open {env.resolve()} and paste your key after OPENROUTER_API_KEY=,\n"
            "save the file, then run start.bat (Windows) or ./start.sh (Mac/Linux) again.",
            file=sys.stderr,
        )
        return 1
    print("[podclip] OPENROUTER_API_KEY looks set. Good to go.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

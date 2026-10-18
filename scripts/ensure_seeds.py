"""Add any missing agent seed to .env. Existing lines are never changed and values are never printed.

A seed fixes an agent's address, so copy the finished .env to the backup laptop as is.
Seeds from a legacy .seeds.local.json are moved into .env (keeping today's addresses and mailboxes),
then that file is deleted.

Usage: .venv/bin/python scripts/ensure_seeds.py
"""
import json
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.runtime import AGENTS  # noqa: E402

ENV = ROOT / ".env"
LEGACY = ROOT / ".seeds.local.json"


def main() -> None:
    present = dotenv_values(ENV) if ENV.exists() else {}
    legacy = json.loads(LEGACY.read_text()) if LEGACY.exists() else {}
    new_lines = []
    for key, spec in AGENTS.items():
        var = spec["seed_env"]
        if present.get(var):
            print(f"kept {var}")
            continue
        if legacy.get(var):
            new_lines.append(f"{var}={legacy[var]}")
            print(f"added {var} (moved from {LEGACY.name})")
        else:
            new_lines.append(f"{var}=rooted-{key}-{secrets.token_hex(24)}")
            print(f"added {var}")
    if new_lines:
        text = ENV.read_text() if ENV.exists() else ""
        prefix = "" if not text or text.endswith("\n") else "\n"
        with ENV.open("a") as f:
            f.write(prefix + "\n".join(new_lines) + "\n")
    if LEGACY.exists():
        LEGACY.unlink()
        print(f"removed {LEGACY.name}")


if __name__ == "__main__":
    main()

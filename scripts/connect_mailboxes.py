"""Connect running local agents to their Agentverse mailboxes without the inspector.

Does the same thing as the inspector's "Connect -> Mailbox" button: POST /connect on the agent's
local port with the Agentverse token. Run it once after the agents start for the first time.

Usage: .venv/bin/python scripts/connect_mailboxes.py [port[:address] ...]   (default: every agent in run_all.AGENTS)
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")


def connect(port: int, token: str, address: str | None = None) -> str:
    # A Bureau serves several agents on one port and routes REST calls by this header.
    headers = {"x-uagents-address": address} if address else {}
    try:
        r = requests.post(
            f"http://127.0.0.1:{port}/connect",
            json={"user_token": token, "agent_type": "mailbox"},
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as e:
        return f"FAIL (agent not reachable: {type(e).__name__})"
    if r.status_code != 200:
        return f"FAIL (HTTP {r.status_code})"
    body = r.json()
    return "OK" if body.get("success") else f"FAIL ({body.get('detail')})"


def main() -> None:
    token = os.getenv("AGENTVERSE_API_KEY")
    if not token:
        sys.exit("AGENTVERSE_API_KEY is not set in .env")
    if len(sys.argv) > 1:
        targets = []
        for arg in sys.argv[1:]:
            port, _, address = arg.partition(":")
            targets.append((f"port {port}", int(port), address or None))
    else:
        from scripts.run_all import AGENTS

        targets = [(a["name"], a["port"], None) for a in AGENTS]
    for name, port, address in targets:
        print(f"{name:24s} :{port}  {connect(port, token, address)}")


if __name__ == "__main__":
    main()

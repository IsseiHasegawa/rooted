"""Reset Rooted between demo runs: forget every chat session and expire open Stripe test checkouts.

Usage: stop scripts/run_all.py (Ctrl+C), then
    .venv/bin/python scripts/demo_reset.py [--dry-run]
and start run_all.py again. uagents keeps storage in memory and only writes it to disk, so a running
agent would put the old sessions straight back; the script refuses to run while any Rooted port is open.
"""
import json
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.runtime import AGENTS, address_of  # noqa: E402

# Per-chat state only; anything else in an agent's storage is kept.
SESSION_PREFIXES = ("session:", "active:", "checkout:", "join:", "payer:", "quote:")


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def clear_storage(dry_run: bool) -> int:
    removed = 0
    for key in AGENTS:
        # uagents names the store after the first 16 characters of the agent address.
        path = ROOT / f"{address_of(key)[:16]}_data.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text() or "{}")
        keep = {k: v for k, v in data.items() if not k.startswith(SESSION_PREFIXES)}
        gone = len(data) - len(keep)
        removed += gone
        print(f"{AGENTS[key]['name']:20s} {gone} session keys removed")
        if gone and not dry_run:
            path.write_text(json.dumps(keep))
    return removed


def expire_checkouts(dry_run: bool) -> int:
    from agents.orchestrator import payments

    if not os.getenv("STRIPE_SECRET_KEY", "").startswith(("sk_test_", "rk_test_")):
        print("Stripe: skipped (STRIPE_SECRET_KEY is missing or not a test-mode key)")
        return 0
    stripe = payments._stripe()
    expired = 0
    try:
        for cs in stripe.checkout.Session.list(status="open", limit=100).auto_paging_iter():
            meta = cs.metadata.to_dict() if cs.metadata else {}  # stripe 16 objects are not dicts
            if meta.get("kind") not in ("deposit", "balance") or not meta.get("session_id"):
                continue  # not a Rooted checkout
            if not dry_run:
                stripe.checkout.Session.expire(cs.id)
            expired += 1
    except Exception as e:  # report the type only: Stripe errors can echo request details
        print(f"Stripe: stopped after {expired} expired ({type(e).__name__})")
        return expired
    print(f"Stripe: {expired} open Rooted checkout sessions {'would be ' if dry_run else ''}expired")
    return expired


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    running = [f"{s['name']} ({s['port']})" for s in AGENTS.values() if port_open(s["port"])]
    if running and not dry_run:
        sys.exit(f"Stop run_all.py first; still running: {', '.join(running)}")
    clear_storage(dry_run)
    expire_checkouts(dry_run)
    print("dry run, nothing changed" if dry_run else "reset done; start scripts/run_all.py again")


if __name__ == "__main__":
    main()

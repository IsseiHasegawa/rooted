"""Drive Rooted agents without ASI:One.

P1 smoke test: sends one request to every stub agent in parallel and checks each one replies.
Start the agents first (scripts/run_all.py, then scripts/connect_mailboxes.py once).

Usage: .venv/bin/python tests/sim_client.py
"""
import asyncio
import os
import secrets
import sys
import time
import traceback
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from uagents import Agent, Context  # noqa: E402
from uagents_core.contrib.protocols.chat import ChatMessage  # noqa: E402

from shared.models import (  # noqa: E402
    AssessRequest,
    AssessResult,
    BookConfirm,
    BookRequest,
    CounterOffer,
    FundingRequest,
    FundingResult,
    LineItem,
    Quote,
    QuoteRequest,
    RenderRequest,
    RenderResult,
    RevisedQuote,
)
from shared.runtime import LocalFirstResolver, address_of, text_message  # noqa: E402

TIMEOUT = int(os.getenv("SIM_TIMEOUT", "30"))
SIM_PORT = int(os.getenv("SIM_PORT", "8100"))

ITEMS = [
    LineItem(id="grab_bar_tub", label="Grab bar inside the tub", category="grab_bar", unit_price_cents=30_000),
    LineItem(id="grab_bar_toilet", label="Grab bar next to the toilet", category="grab_bar", unit_price_cents=30_000),
    LineItem(id="night_light", label="Night light on the bed-to-bath path", category="lighting", unit_price_cents=4_000),
]

sim = Agent(
    name="rooted-sim-client",
    seed=f"rooted-sim-client-{secrets.token_hex(16)}",
    port=SIM_PORT,
    endpoint=[f"http://127.0.0.1:{SIM_PORT}/submit"],
    resolve=LocalFirstResolver(),
)


def checks(session_id: str) -> list[tuple[str, str, object, object]]:
    return [
        ("orchestrator", "orchestrator", text_message("My mom lives alone and I'm worried about her bathroom."), ChatMessage),
        ("risk", "risk", AssessRequest(session_id=session_id, answers={"fell_past_year": True}, concerns="slippery tub"), AssessResult),
        ("visualizer", "visualizer", RenderRequest(session_id=session_id, bathroom_id="b1", fix_item_ids=[i.id for i in ITEMS]), RenderResult),
        ("funding", "funding", FundingRequest(session_id=session_id, age=82, community="East Los Angeles", tenure="owner", months_owned=240, household_size=1, annual_income=38_000, insurance_plan="sample_ma_plan_a", line_items=ITEMS), FundingResult),
        ("juniper", "juniper", QuoteRequest(session_id=session_id, line_items=ITEMS, zip_code="90022"), Quote),
        ("mesa", "mesa", QuoteRequest(session_id=session_id, line_items=ITEMS, zip_code="90022"), Quote),
        ("arbor", "arbor", QuoteRequest(session_id=session_id, line_items=ITEMS, zip_code="90022"), Quote),
    ]


async def run_one(ctx: Context, label: str, key: str, msg, reply_type):
    t0 = time.monotonic()
    reply, status = await ctx.send_and_receive(address_of(key), msg, response_type=reply_type, timeout=TIMEOUT)
    return label, reply, status, time.monotonic() - t0


@sim.on_event("startup")
async def main(ctx: Context):
    # uagents swallows startup exceptions and keeps serving, so exit explicitly either way.
    try:
        await smoke(ctx)
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(2)


async def smoke(ctx: Context):
    await asyncio.sleep(3)  # let the Almanac registration of our local endpoint land
    session_id = str(uuid4())
    results = await asyncio.gather(*(run_one(ctx, *c) for c in checks(session_id)))

    # Follow-up messages that need a quote id from the first round.
    arbor = next((r for label, r, _, _ in results if label == "arbor"), None)
    if arbor is not None:
        follow_ups = [
            ("arbor counter", "arbor", CounterOffer(session_id=session_id, quote_id=arbor.quote_id, target_cents=int(arbor.total_cents * 0.95)), RevisedQuote),
            ("arbor book", "arbor", BookRequest(session_id=session_id, quote_id=arbor.quote_id, date=arbor.earliest_date), BookConfirm),
        ]
        # Sequential: replies are matched per (destination, session), so two waits on arbor would collide.
        for c in follow_ups:
            results.append(await run_one(ctx, *c))

    failed = 0
    print(f"\nsession {session_id}")
    for label, reply, status, elapsed in results:
        ok = reply is not None
        failed += not ok
        detail = type(reply).__name__ if ok else f"no reply ({status.detail})"
        print(f"{'PASS' if ok else 'FAIL'}  {label:16s} {elapsed:5.1f}s  {detail}")
    print(f"\n{len(results) - failed}/{len(results)} replied")
    sys.stdout.flush()
    os._exit(1 if failed else 0)


if __name__ == "__main__":
    sim.run()

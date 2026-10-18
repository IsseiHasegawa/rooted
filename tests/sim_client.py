"""Drive Rooted agents without ASI:One.

smoke: sends one request to every agent in parallel and checks each one replies.
flow:  plays the daughter in one chat session as Maria's family, from INTAKE to SPLIT, clicking each card.
Start the agents first (scripts/run_all.py).

Usage: .venv/bin/python tests/sim_client.py [smoke|flow|all]   (default: all)
"""
import asyncio
import json
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
from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, MetadataContent  # noqa: E402

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
from shared.runtime import LocalFirstResolver, address_of, message_text, text_message  # noqa: E402

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


MODE = sys.argv[1] if len(sys.argv) > 1 else "all"
inbox: asyncio.Queue = asyncio.Queue()


@sim.on_message(ChatMessage)
async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
    await inbox.put(msg)


@sim.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    return


@sim.on_event("startup")
async def main(ctx: Context):
    # uagents swallows startup exceptions and keeps serving, so exit explicitly either way.
    try:
        await asyncio.sleep(3)  # let the Almanac registration of our local endpoint land
        failed = 0
        if MODE in ("smoke", "all"):
            failed += await smoke(ctx)
        if MODE in ("flow", "all"):
            failed += await flow(ctx)
    except Exception:
        traceback.print_exc()
        failed = 1
    sys.stdout.flush()
    os._exit(1 if failed else 0)


async def smoke(ctx: Context) -> int:
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
    return failed


# ---- flow ------------------------------------------------------------------

MARIA = {
    "parent_name": "Maria", "age": "82", "community": "East Los Angeles", "zip_code": "90022", "tenure": "owner",
    "months_owned": "240", "household_size": "1", "annual_income": "38000", "insurance_plan": "sample_ma_plan_a",
    "concern": "Slippery tub and a dark hallway at night",
}
STEADI_YES = {"q1_fallen", "q3_unsteady", "q4_furniture", "q5_worried", "q8_rush_toilet"}


def card_of(msg: ChatMessage) -> tuple[str, dict] | None:
    for c in msg.content:
        if isinstance(c, MetadataContent) and "card_payload" in c.metadata:
            return c.metadata["card_kind"], json.loads(c.metadata["card_payload"])
    return None


async def next_card(timeout: float = 90) -> tuple[list[str], tuple[str, dict]]:
    """Collect chat text until the next card arrives."""
    texts = []
    deadline = time.monotonic() + timeout
    while True:
        msg = await asyncio.wait_for(inbox.get(), max(0.1, deadline - time.monotonic()))
        texts.append(message_text(msg))
        card = card_of(msg)
        if card:
            return [t for t in texts if t], card


def find_selections(node) -> list[dict]:
    """Every CTA selection in a payload, in document order."""
    found = []
    if isinstance(node, dict):
        if isinstance(node.get("selection"), dict) and "action" in node["selection"]:
            found.append(node["selection"])
        for v in node.values():
            found += find_selections(v)
    elif isinstance(node, list):
        for v in node:
            found += find_selections(v)
    return found


def pick(payload: dict, action: str) -> dict:
    return next(s for s in find_selections(payload) if s["action"] == action)


async def flow(ctx: Context) -> int:
    orch = address_of("orchestrator")
    steps = [
        # (label, expected action on the card shown, how to answer it)
        ("INTAKE", "submit_intake", lambda p: {**pick(p, "submit_intake"), **MARIA}),
        ("STEADI_1", "submit_steadi", lambda p: {**pick(p, "submit_steadi"), **{f["name"]: f["name"] in STEADI_YES for f in p["fields"]}}),
        ("STEADI_2", "submit_steadi", lambda p: {**pick(p, "submit_steadi"), **{f["name"]: f["name"] in STEADI_YES for f in p["fields"]}}),
        ("RISK_RESULT", "continue", lambda p: pick(p, "continue")),
        ("PICK_BATHROOM", "pick_bathroom", lambda p: {**pick(p, "pick_bathroom"), "bathroom_id": "b1"}),
        ("RENDER", "get_quotes", lambda p: {**pick(p, "get_quotes"), "scope": "urgent"}),
        ("QUOTES", "pick_quote", lambda p: pick(p, "pick_quote")),
        ("FUNDING", "accept_funding", lambda p: pick(p, "accept_funding")),
        ("REVIEW", "approve", lambda p: pick(p, "approve")),
        ("CONSENT", "consent", lambda p: pick(p, "consent")),
        ("SPLIT", "submit_split", lambda p: {**pick(p, "submit_split"), "payers": "2", "split": "even", "payer_contacts": "@leo"}),
    ]
    print("\nflow: Maria, East Los Angeles")
    await ctx.send(orch, text_message("Start over. My mom lives alone and I'm worried about her bathroom."))
    # An unparseable reply must bring the same card back.
    await next_card()
    await ctx.send(orch, text_message("what is the weather today?"))
    for label, action, answer in steps:
        t0 = time.monotonic()
        try:
            texts, (kind, payload) = await next_card()
        except asyncio.TimeoutError:
            print(f"FAIL  {label:14s} no card arrived")
            return 1
        if not any(s["action"] == action for s in find_selections(payload)):
            print(f"FAIL  {label:14s} got a {kind} card without {action}: {texts}")
            return 1
        sel = answer(payload)
        print(f"PASS  {label:14s} {time.monotonic() - t0:5.1f}s  {kind:8s} -> {action}  {' | '.join(texts)[:90]}")
        if kind == "carousel":
            for line in texts:
                print(f"        chat: {line}")
            for item in payload["items"]:
                badges = ", ".join(b["label"] for b in item["badges"])
                print(f"        quote: {item['title']}: {item['secondary_text']} [{badges}]")
        await ctx.send(orch, text_message(json.dumps(sel)))
    texts, (kind, payload) = await next_card()
    print(f"PASS  {'PAY':14s}        {kind:8s} {payload['root'].get('title')}: {payload['root'].get('subtitle')}  {' | '.join(texts)[:60]}")
    print("flow reached PAY")
    return 0


if __name__ == "__main__":
    sim.run()

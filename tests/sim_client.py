"""Drive Rooted agents without ASI:One.

smoke: sends one request to every agent in parallel and checks each one replies.
flow:  plays the daughter in one chat session as Maria's family, from INTAKE to SPLIT, clicking each card.
pay:   flow, then this chat's deposit share, booking, /complete and its balance share with stubbed Stripe
       (the other payer's share stays pending). Needs the orchestrator started with ROOTED_PAYMENTS=stub.
Start the agents first (scripts/run_all.py).

Usage: .venv/bin/python tests/sim_client.py [smoke|flow|pay|all]   (default: all = smoke + flow)
"""
import asyncio
import json
import os
import re
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
from uagents_core.contrib.protocols.payment import (  # noqa: E402
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RequestPayment,
)

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
        ("risk", "risk", AssessRequest(session_id=session_id, answers={"q1_fallen": True}, concerns="slippery tub"), AssessResult),
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
pay_inbox: asyncio.Queue = asyncio.Queue()


@sim.on_message(ChatMessage)
async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
    await inbox.put(msg)


@sim.on_message(RequestPayment)
async def on_request_payment(ctx: Context, sender: str, msg: RequestPayment):
    await pay_inbox.put(msg)


@sim.on_message(CompletePayment)
async def on_complete_payment(ctx: Context, sender: str, msg: CompletePayment):
    await pay_inbox.put(msg)


@sim.on_message(CancelPayment)
async def on_cancel_payment(ctx: Context, sender: str, msg: CancelPayment):
    await pay_inbox.put(msg)


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
        if MODE in ("flow", "all", "pay"):
            failed += await flow(ctx)
        if MODE == "pay" and not failed:
            failed += await pay(ctx)
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

SIM_SCOPE = os.getenv("SIM_SCOPE", "full")  # urgent | full; default is the scripted demo path
SIM_PICK = os.getenv("SIM_PICK", "arbor")  # juniper | mesa | arbor; "" picks the first quote
SIM_CHOICE = os.getenv("SIM_CHOICE", "fix_now")  # fix_now (demo path) | wait_picf (ends on the PICF checklist)

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


def funding_numbers(payload: dict) -> dict:
    """The waterfall inputs as shown on the funding card: badges, then each program in card order."""
    def walk(node):
        if isinstance(node, dict):
            yield node
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)
    badges = [n["label"] for n in walk(payload) if n.get("type") == "badge"]
    programs = []
    for n in walk(payload):
        kids = n.get("children") if isinstance(n.get("children"), list) else []
        if kids and kids[0].get("style") == "emphasis" and len(kids) > 1 and kids[1].get("type") == "badge":
            programs.append({"name": kids[0]["value"], "amount": kids[1]["label"]})
    return {"badges": badges[:3], "programs": programs}


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
        ("RENDER", "get_quotes", lambda p: {**pick(p, "get_quotes"), "scope": SIM_SCOPE}),
        ("QUOTES", "pick_quote", lambda p: next(s for s in find_selections(p) if s["action"] == "pick_quote"
                                                and s.get("contractor_id") == SIM_PICK) if SIM_PICK else pick(p, "pick_quote")),
        ("FUNDING", "accept_funding", lambda p: next(s for s in find_selections(p) if s["action"] == "accept_funding"
                                                     and s.get("choice") == SIM_CHOICE)),
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
        if label == "FUNDING":
            print(f"        funding: {json.dumps(funding_numbers(payload))}")
        if kind == "carousel":
            for line in texts:
                print(f"        chat: {line}")
            for item in payload["items"]:
                badges = ", ".join(b["label"] for b in item["badges"])
                print(f"        quote: {item['title']}: {item['secondary_text']} [{badges}]")
        if label == "CONSENT":
            # A dismissed consent card (approved=false) must come back, not advance to SPLIT.
            await ctx.send(orch, text_message(json.dumps({"selection": sel, "approved": False})))
            _, (kind2, payload2) = await next_card()
            if not any(s["action"] == "consent" for s in find_selections(payload2)):
                print(f"FAIL  {'CONSENT':14s} approved=false advanced to a {kind2} card")
                return 1
            print(f"PASS  {'CONSENT':14s}        approved=false -> consent card re-sent")
            sel = {"selection": sel, "approved": True}
        await ctx.send(orch, text_message(json.dumps(sel)))
        if label == "FUNDING" and SIM_CHOICE == "wait_picf":
            texts, (kind, payload) = await next_card()
            title = payload.get("root", {}).get("title", "")
            ok = title.startswith("Waiting for") and "Nothing has been charged" in json.dumps(payload)
            print(f"{'PASS' if ok else 'FAIL'}  {'WAIT_PICF':14s}        {kind:8s} {title}  {' | '.join(texts)[:60]}")
            return 0 if ok else 1
    texts, (kind, payload) = await next_card()
    print(f"PASS  {'PAY':14s}        {kind:8s} {payload['root'].get('title')}: {payload['root'].get('subtitle')}  {' | '.join(texts)[:60]}")
    print("flow reached PAY")
    return 0


# ---- pay (stubbed Stripe) --------------------------------------------------

async def next_payment(kind: type, timeout: float = 30):
    """Next payment-protocol message, which must be of the given type."""
    msg = await asyncio.wait_for(pay_inbox.get(), timeout)
    if not isinstance(msg, kind):
        raise AssertionError(f"expected {kind.__name__}, got {type(msg).__name__}")
    return msg


async def drain_chat(seconds: float = 2) -> list[str]:
    texts = []
    try:
        while True:
            texts.append(message_text(await asyncio.wait_for(inbox.get(), seconds)))
    except asyncio.TimeoutError:
        return texts


def commit_for(req: RequestPayment, orch: str) -> CommitPayment:
    stripe = req.metadata["stripe"]
    return CommitPayment(funds=Funds(currency="USD", amount=req.accepted_funds[0].amount, payment_method="stripe"),
                         recipient=orch, transaction_id=stripe["checkout_session_id"], reference=req.reference)


async def card_titled(title: str, timeout: float = 60) -> tuple[list[str], tuple[str, dict]]:
    """Skip status cards until the custom card with this title arrives."""
    deadline = time.monotonic() + timeout
    seen = []
    while True:
        texts, (kind, payload) = await next_card(max(0.1, deadline - time.monotonic()))
        seen += texts
        if payload.get("root", {}).get("title") == title:
            return seen, (kind, payload)


async def pay_one(ctx: Context, orch: str, req: RequestPayment, label: str, early_commit: bool = False) -> None:
    stripe = req.metadata["stripe"]
    keys = {"ui_mode", "publishable_key", "client_secret", "checkout_session_id", "currency", "amount_cents"}
    assert keys <= set(stripe), f"metadata.stripe missing {keys - set(stripe)}"
    assert str(stripe["amount_cents"]) == str(round(float(req.accepted_funds[0].amount) * 100)), "amount mismatch"
    cs = stripe["checkout_session_id"]
    if early_commit:
        await ctx.send(orch, commit_for(req, orch))
        cancel = await next_payment(CancelPayment)
        print(f"PASS  {label:14s}        commit before checkout -> CancelPayment: {cancel.reason}")
    await ctx.send(orch, text_message(f"/stub-paid {cs}"))
    await drain_chat(1)
    await ctx.send(orch, commit_for(req, orch))
    await next_payment(CompletePayment)
    print(f"PASS  {label:14s}        ${int(stripe['amount_cents']) / 100:.2f} {cs} -> CompletePayment")


async def pay(ctx: Context) -> int:
    orch = address_of("orchestrator")
    try:
        req0 = await next_payment(RequestPayment)
        print(f"PASS  {'DEPOSIT':14s}        RequestPayment {req0.accepted_funds[0].amount} USD (this chat's share only)")
        await pay_one(ctx, orch, req0, "DEPOSIT", early_commit=True)
        texts, (kind, payload) = await card_titled("Booked")
        body = json.dumps(payload)
        assert "share is pending; Rooted will collect it separately." in body, "booking card does not mention the pending share"
        print(f"PASS  {'BOOKED':14s}        {kind:8s} {payload['root'].get('subtitle')}  {' | '.join(texts)[:70]}")
        try:
            extra = await next_payment(RequestPayment, timeout=2)
            raise AssertionError(f"unexpected RequestPayment for {extra.accepted_funds[0].amount}")
        except asyncio.TimeoutError:
            print(f"PASS  {'ONE PAYER':14s}        no RequestPayment for the pending share")
        await ctx.send(orch, text_message("/complete"))
        texts, (kind, payload) = await card_titled("Work completed")
        req = await next_payment(RequestPayment)
        print(f"PASS  {'COMPLETE':14s}        completion card + balance request {req.accepted_funds[0].amount} USD")
        await pay_one(ctx, orch, req, "BALANCE")
        texts = await drain_chat(3)
        assert any("balance share is paid" in t and "Rooted will collect it separately" in t for t in texts), f"no balance-paid message: {texts}"
        print("pay reached DONE")
        return 0
    except (AssertionError, asyncio.TimeoutError) as e:
        print(f"FAIL  pay: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sim.run()

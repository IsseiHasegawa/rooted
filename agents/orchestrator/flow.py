"""Session state machine for rooted-orchestrator.

INTAKE -> STEADI_1 -> STEADI_2 -> RISK_RESULT -> PICK_BATHROOM -> RENDER -> QUOTES -> FUNDING
-> REVIEW -> CONSENT -> SPLIT -> PAY -> BOOKED, plus the terminal LOW_RISK / CANCELLED / DECLINED.
Each state names the card the user is looking at; the matching selection moves to the next state.
"""
import asyncio
import json
import re
from typing import Any

from uagents import Context, Model
from uagents_core.contrib.protocols.chat import ChatMessage

from shared import cards
from shared.models import (
    AssessRequest,
    AssessResult,
    CounterOffer,
    FundingRequest,
    FundingResult,
    LineItem,
    ProgramAmount,
    Quote,
    QuotedItem,
    QuoteRequest,
    RenderRequest,
    RenderResult,
    RevisedQuote,
    WaterfallRequest,
    WaterfallResult,
)
from shared.runtime import CONTRACTOR_KEYS, ROOT, address_of, message_text, text_message

DEPOSIT_PERCENT = 30
TIMEOUTS = {"risk": 15, "visualizer": 40, "funding": 15, "contractor": 20, "chart": 10}

PRICES = {p["id"]: p for p in json.loads((ROOT / "data" / "prices.json").read_text())["items"]}
BATHROOMS = [
    {"id": f"b{i}", "label": label, "image": f"bathrooms/b{i}.png"}
    for i, label in enumerate(["Tub with shower", "Step-in shower", "Small bathroom", "Rug and clutter", "Dim bathroom"], 1)
]
CONTRACTORS = {c["id"]: c for c in json.loads((ROOT / "data" / "contractors.json").read_text())["contractors"]}
FULL_REMODEL_ITEMS = [p["id"] for p in PRICES.values() if p["scope"] == "full"]
RESTART = re.compile(r"\b(start over|restart|new plan|reset)\b", re.I)

# The selection action each state is waiting for.
EXPECTED = {
    "INTAKE": {"submit_intake"},
    "STEADI_1": {"submit_steadi"},
    "STEADI_2": {"submit_steadi"},
    "RISK_RESULT": {"continue"},
    "PICK_BATHROOM": {"pick_bathroom"},
    "RENDER": {"get_quotes"},
    "QUOTES": {"pick_quote"},
    "FUNDING": {"accept_funding"},
    "REVIEW": {"approve", "cancel"},
    "CONSENT": {"consent", "decline"},
    "SPLIT": {"submit_split"},
}
TERMINAL = {"LOW_RISK", "CANCELLED", "DECLINED", "PAY", "BOOKED"}


# ---- storage ---------------------------------------------------------------

def _key(session_id: str) -> str:
    return f"session:{session_id}"


def _active_key(sender: str) -> str:
    return f"active:{sender}"


def load(ctx: Context, session_id: str) -> dict | None:
    return ctx.storage.get(_key(session_id))


def save(ctx: Context, s: dict) -> None:
    ctx.storage.set(_key(s["session_id"]), s)


def new_session(ctx: Context, sender: str, session_id: str) -> dict:
    s = {"session_id": session_id, "sender": sender, "state": "NEW", "data": {}, "card": None}
    ctx.storage.set(_active_key(sender), session_id)
    return s


def transition(ctx: Context, s: dict, new_state: str) -> None:
    ctx.logger.info(f"[{s['session_id']}] {s['state']} -> {new_state}")
    s["state"] = new_state
    save(ctx, s)


# ---- selection parsing -----------------------------------------------------

def _truthy(v: Any) -> bool:
    return v is True or str(v).strip().lower() in ("true", "yes", "on", "1", "checked")


def parse(text: str) -> dict | None:
    sel = cards.parse_selection(text)
    if not sel:
        return None
    # Forms may return field values next to the selection or nested under one of these keys.
    for nested in ("values", "fields", "form", "form_values", "data"):
        if isinstance(sel.get(nested), dict):
            sel = {**sel[nested], **{k: v for k, v in sel.items() if k != nested}}
    return sel


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(float(str(v).replace(",", "").replace("$", "").strip()))
    except (TypeError, ValueError):
        return default


# ---- outbound helpers ------------------------------------------------------

async def say(ctx: Context, s: dict, text: str) -> None:
    await ctx.send(s["sender"], text_message(text))


async def show(ctx: Context, s: dict, narration: str, card: tuple[str, dict], terminal: bool = False) -> None:
    kind, payload = card
    s["card"] = {"narration": narration, "kind": kind, "payload": payload, "terminal": terminal}
    await ctx.send(s["sender"], cards.card_message(narration, kind, payload, terminal=terminal))


async def resend_card(ctx: Context, s: dict, note: str) -> None:
    c = s.get("card")
    if not c:
        await say(ctx, s, note)
        return
    await ctx.send(s["sender"], cards.card_message(f"{note}\n\n{c['narration']}".strip(), c["kind"], c["payload"], terminal=c["terminal"]))


async def call(ctx: Context, s: dict, key: str, msg: Model, reply_type: type[Model], timeout: int) -> Model | None:
    reply, status = await ctx.send_and_receive(address_of(key), msg, response_type=reply_type, timeout=timeout)
    if reply is None:
        ctx.logger.warning(f"[{s['session_id']}] {key} gave no {reply_type.__name__}: {status.detail}")
    return reply


def line_items(item_ids: list[str]) -> list[LineItem]:
    return [
        LineItem(id=i, label=PRICES[i]["label"], category=PRICES[i]["category"],
                 unit_price_cents=PRICES[i]["unit_price_cents"], structural=PRICES[i]["structural"])
        for i in item_ids if i in PRICES
    ]


def fix_list(item_ids: list[str]) -> list[dict]:
    return [{"id": i, "label": PRICES[i]["label"]} for i in item_ids if i in PRICES]


# ---- state handlers --------------------------------------------------------

async def start(ctx: Context, s: dict) -> None:
    await show(ctx, s, "Hi, I'm Rooted. I help families make an older parent's home safer so they can keep living there. "
                       "First, a few questions about your parent.", cards.intake_card(s["session_id"]))
    transition(ctx, s, "INTAKE")


async def on_intake(ctx: Context, s: dict, sel: dict) -> None:
    s["data"]["intake"] = {
        "parent_name": str(sel.get("parent_name") or "your parent").strip() or "your parent",
        "age": _int(sel.get("age")),
        "community": str(sel.get("community") or "").strip(),
        "zip_code": str(sel.get("zip_code") or "").strip(),
        "tenure": "renter" if str(sel.get("tenure", "")).lower().startswith("rent") else "owner",
        "months_owned": _int(sel.get("months_owned")),
        "household_size": max(1, _int(sel.get("household_size"), 1)),
        "annual_income": _int(sel.get("annual_income")),
        "insurance_plan": str(sel.get("insurance_plan") or "none"),
        "concern": str(sel.get("concern") or ""),
    }
    await show(ctx, s, "Thanks. Next, the CDC's 12-question fall-risk check.", cards.steadi_card(s["session_id"], 1))
    transition(ctx, s, "STEADI_1")


def _steadi_answers(sel: dict) -> dict[str, bool]:
    return {k: _truthy(v) for k, v in sel.items() if re.fullmatch(r"q\d{1,2}_[a-z_]+", k)}


async def on_steadi_1(ctx: Context, s: dict, sel: dict) -> None:
    s["data"]["steadi"] = _steadi_answers(sel)
    await show(ctx, s, "Six more.", cards.steadi_card(s["session_id"], 2))
    transition(ctx, s, "STEADI_2")


async def on_steadi_2(ctx: Context, s: dict, sel: dict) -> None:
    s["data"]["steadi"] = {**s["data"].get("steadi", {}), **_steadi_answers(sel)}
    save(ctx, s)
    sid = s["session_id"]
    result = await call(ctx, s, "risk", AssessRequest(session_id=sid, answers=s["data"]["steadi"],
                                                     concerns=s["data"]["intake"].get("concern", "")),
                        AssessResult, TIMEOUTS["risk"])
    if result is None or result.error:
        await resend_card(ctx, s, "Sorry, I couldn't score the check just now. Please submit it again.")
        return
    hazards = [h.dict() for h in result.hazards]
    s["data"]["risk"] = {"score": result.score, "at_risk": result.at_risk, "hazards": hazards}
    if not result.at_risk:
        await show(ctx, s, "Good news.", cards.low_risk_card(sid, result.score, result.source_url), terminal=True)
        transition(ctx, s, "LOW_RISK")
        return
    await show(ctx, s, "Here is what the check found.",
               cards.risk_result_card(sid, result.score, True, hazards, result.source_url, fell_past_year=result.fell_past_year))
    transition(ctx, s, "RISK_RESULT")


async def on_continue(ctx: Context, s: dict, sel: dict) -> None:
    name = s["data"]["intake"]["parent_name"]
    await show(ctx, s, "Most falls at home happen in the bathroom, so let's start there.",
               cards.bathroom_pick_card(s["session_id"], BATHROOMS, name))
    transition(ctx, s, "PICK_BATHROOM")


async def on_pick_bathroom(ctx: Context, s: dict, sel: dict) -> None:
    sid = s["session_id"]
    bathroom_id = sel.get("bathroom_id") if sel.get("bathroom_id") in {b["id"] for b in BATHROOMS} else None
    if not bathroom_id:
        await resend_card(ctx, s, "Please pick one of the bathrooms.")
        return
    fix_ids: list[str] = []
    for h in s["data"]["risk"]["hazards"]:
        fix_ids += [f for f in h.get("fix_item_ids", []) if f in PRICES and f not in fix_ids]
    fix_ids = fix_ids or ["grab_bar_tub", "grab_bar_toilet", "night_light"]
    s["data"].update(bathroom_id=bathroom_id, fix_item_ids=fix_ids)
    save(ctx, s)
    await say(ctx, s, "Drawing the safer version of this bathroom...")
    render = await call(ctx, s, "visualizer", RenderRequest(session_id=sid, bathroom_id=bathroom_id, fix_item_ids=fix_ids),
                        RenderResult, TIMEOUTS["visualizer"])
    marked = render.marked_url if render and not render.error else f"bathrooms/{bathroom_id}.png"
    urgent = sum(i.unit_price_cents for i in line_items(fix_ids))
    full = urgent + sum(i.unit_price_cents for i in line_items(FULL_REMODEL_ITEMS))
    await show(ctx, s, "The numbers on the picture match the list of fixes.",
               cards.after_detail_card(sid, bathroom_id, marked, fix_list(fix_ids), urgent, full))
    transition(ctx, s, "RENDER")


async def on_get_quotes(ctx: Context, s: dict, sel: dict) -> None:
    sid = s["session_id"]
    scope = "full" if sel.get("scope") == "full" else "urgent"
    item_ids = list(s["data"]["fix_item_ids"]) + (FULL_REMODEL_ITEMS if scope == "full" else [])
    s["data"].update(scope=scope, item_ids=item_ids)
    save(ctx, s)
    await say(ctx, s, "Checking 3 contractors...")
    req = QuoteRequest(session_id=sid, line_items=line_items(item_ids), zip_code=s["data"]["intake"]["zip_code"], scope=scope)
    replies = await asyncio.gather(*(call(ctx, s, k, req, Quote, TIMEOUTS["contractor"]) for k in CONTRACTOR_KEYS))
    quotes = [q for q in replies if q is not None]
    if not quotes:
        await resend_card(ctx, s, "No contractor answered in time. Please try again.")
        return
    missing = [CONTRACTORS[k]["name"] for k, q in zip(CONTRACTOR_KEYS, replies) if q is None]
    if missing:
        ctx.logger.info(f"[{sid}] failover: no quote from {', '.join(missing)}, continuing with {len(quotes)}")
        await say(ctx, s, f"{' and '.join(missing)} didn't answer within {TIMEOUTS['contractor']} seconds, "
                          f"so I'm comparing the other {len(quotes)}.")
    quotes = await negotiate(ctx, s, quotes)
    s["data"]["quotes"] = {q.quote_id: q.dict() for q in quotes}
    await show(ctx, s, f"{len(quotes)} contractor{'s' if len(quotes) != 1 else ''} answered.", cards.quotes_carousel_card(sid, quotes))
    transition(ctx, s, "QUOTES")


async def negotiate(ctx: Context, s: dict, quotes: list[Quote]) -> list[Quote]:
    """One round: ask the CAPS-certified contractor to match the lowest other quote."""
    caps = next((q for q in quotes if CONTRACTORS.get(q.contractor_id, {}).get("caps_certified")), None)
    others = [q.total_cents for q in quotes if q is not caps]
    if caps is None or not others or min(others) >= caps.total_cents:
        return quotes
    target = min(others)
    revised = await call(ctx, s, caps.contractor_id, CounterOffer(session_id=s["session_id"], quote_id=caps.quote_id, target_cents=target),
                         RevisedQuote, TIMEOUTS["contractor"])
    if revised is None or not revised.total_cents or revised.total_cents >= caps.total_cents:
        await say(ctx, s, f"I asked {caps.contractor_name} for a better price; they kept {cards.usd(caps.total_cents)}.")
        return quotes
    ctx.logger.info(f"[{s['session_id']}] negotiation {caps.quote_id}: {caps.total_cents} -> {revised.total_cents} accepted={revised.accepted}")
    verb = "agreed to match" if revised.accepted else "came down to"
    await say(ctx, s, f"I asked {caps.contractor_name} to match {cards.usd(target)}. They {verb} "
                      f"{cards.usd(revised.total_cents)} (was {cards.usd(caps.total_cents)}).")
    # Spread the discount over the items so the line items still add up to the total.
    ratio = revised.total_cents / caps.total_cents
    items = [QuotedItem(item_id=i.item_id, label=i.label, price_cents=round(i.price_cents * ratio)) for i in caps.items]
    if items:
        items[-1].price_cents += revised.total_cents - sum(i.price_cents for i in items)
    updated = Quote(**{**caps.dict(), "total_cents": revised.total_cents, "items": [i.dict() for i in items],
                       "badges": caps.badges + ["Negotiated"]})
    return [updated if q is caps else q for q in quotes]


async def on_pick_quote(ctx: Context, s: dict, sel: dict) -> None:
    sid = s["session_id"]
    quotes = s["data"]["quotes"]
    quote = quotes.get(sel.get("quote_id")) or next(
        (q for q in quotes.values() if q["contractor_id"] == sel.get("contractor_id")), None)
    if not quote:
        await resend_card(ctx, s, "Please pick one of the quotes.")
        return
    s["data"]["quote_id"] = quote["quote_id"]
    save(ctx, s)
    intake = s["data"]["intake"]
    priced = {i["item_id"]: i["price_cents"] for i in quote["items"]}
    items = [LineItem(**{**li.dict(), "unit_price_cents": priced.get(li.id, li.unit_price_cents)})
             for li in line_items(s["data"]["item_ids"])]
    funding = await call(ctx, s, "funding", FundingRequest(
        session_id=sid, age=intake["age"], community=intake["community"], tenure=intake["tenure"],
        months_owned=intake["months_owned"], household_size=intake["household_size"],
        annual_income=intake["annual_income"], insurance_plan=intake["insurance_plan"], line_items=items,
    ), FundingResult, TIMEOUTS["funding"])
    if funding is None or funding.error:
        funding = FundingResult(session_id=sid, total_cents=quote["total_cents"], family_share_cents=quote["total_cents"])
        await say(ctx, s, "I couldn't check funding programs just now, so this shows the full price.")
    if funding.matches and not funding.chart_url:
        chart = await call(ctx, s, "visualizer", WaterfallRequest(
            session_id=sid, total_cents=funding.total_cents, family_share_cents=funding.family_share_cents,
            program_amounts=[ProgramAmount(name=m.name, amount_cents=m.amount_cents) for m in funding.matches],
        ), WaterfallResult, TIMEOUTS["chart"])
        if chart and chart.chart_url and not chart.error:
            funding.chart_url = chart.chart_url
    s["data"]["funding"] = funding.dict()
    await show(ctx, s, "Here is who can help pay for it.", cards.funding_card(sid, quote["quote_id"], funding))
    transition(ctx, s, "FUNDING")


def _plan(s: dict) -> dict:
    quote = s["data"]["quotes"][s["data"]["quote_id"]]
    f = s["data"]["funding"]
    return {
        "quote": quote,
        "items": [i["label"] for i in quote["items"]],
        "family_share": f["family_share_cents"],
        "deposit": f["family_share_cents"] * DEPOSIT_PERCENT // 100,
    }


async def on_accept_funding(ctx: Context, s: dict, sel: dict) -> None:
    s["data"]["prepare_packet"] = sel.get("prepare_packet") == "yes"
    p, f = _plan(s), s["data"]["funding"]
    await show(ctx, s, "Please review the plan.", cards.plan_review_card(
        s["session_id"], p["quote"]["quote_id"], p["items"], p["quote"]["contractor_name"], p["quote"]["earliest_date"],
        f["total_cents"], f["covered_cents"], f["family_share_cents"], DEPOSIT_PERCENT))
    transition(ctx, s, "REVIEW")


async def on_review(ctx: Context, s: dict, sel: dict) -> None:
    if sel["action"] == "cancel":
        await say(ctx, s, "Okay, nothing is booked. Say \"start over\" any time to make a new plan.")
        transition(ctx, s, "CANCELLED")
        return
    p = _plan(s)
    name = s["data"]["intake"]["parent_name"]
    await show(ctx, s, f"Before anything is booked, {name} should see the plan and agree.",
               cards.consent_card(s["session_id"], p["quote"]["quote_id"], name, p["items"], p["quote"]["earliest_date"]))
    transition(ctx, s, "CONSENT")


async def on_consent(ctx: Context, s: dict, sel: dict) -> None:
    if sel["action"] == "decline":
        await say(ctx, s, "Understood. Nothing is booked. Say \"start over\" when you'd like to revisit the plan.")
        transition(ctx, s, "DECLINED")
        return
    p = _plan(s)
    await show(ctx, s, "Last step: who pays the deposit?", cards.split_card(s["session_id"], p["quote"]["quote_id"], p["deposit"]))
    transition(ctx, s, "SPLIT")


def split_shares(deposit_cents: int, payers: int, percents: list[int] | None = None) -> list[int]:
    """Server-side share amounts in cents; the remainder cents go to the first payers so the sum is exact."""
    payers = max(1, min(payers, 6))
    if percents and len(percents) == payers and sum(percents) == 100:
        shares = [deposit_cents * pct // 100 for pct in percents]
    else:
        shares = [deposit_cents // payers] * payers
    for i in range(deposit_cents - sum(shares)):
        shares[i % payers] += 1
    return shares


async def on_split(ctx: Context, s: dict, sel: dict) -> None:
    p = _plan(s)
    percents = [_int(x) for x in re.findall(r"\d+", str(sel.get("custom_percents") or ""))] if sel.get("split") == "custom" else None
    shares = split_shares(p["deposit"], _int(sel.get("payers"), 1), percents)
    contacts = [c.strip() for c in str(sel.get("payer_contacts") or "").split(",") if c.strip()]
    s["data"]["payers"] = [
        {"name": "You" if i == 0 else (contacts[i - 1] if i - 1 < len(contacts) else f"Payer {i + 1}"),
         "amount_cents": amount, "paid": False}
        for i, amount in enumerate(shares)
    ]
    save(ctx, s)
    await show(ctx, s, "Payment requests are on their way.", cards.payment_status_card(s["session_id"], s["data"]["payers"]), terminal=True)
    transition(ctx, s, "PAY")


HANDLERS = {
    "INTAKE": on_intake,
    "STEADI_1": on_steadi_1,
    "STEADI_2": on_steadi_2,
    "RISK_RESULT": on_continue,
    "PICK_BATHROOM": on_pick_bathroom,
    "RENDER": on_get_quotes,
    "QUOTES": on_pick_quote,
    "FUNDING": on_accept_funding,
    "REVIEW": on_review,
    "CONSENT": on_consent,
    "SPLIT": on_split,
}


# ---- entry point -----------------------------------------------------------

async def handle_chat(ctx: Context, sender: str, msg: ChatMessage) -> None:
    text = message_text(msg)
    sel = parse(text)
    session_id = (sel or {}).get("session_id")
    s = load(ctx, session_id) if session_id else None
    if s is None:
        active = ctx.storage.get(_active_key(sender))
        s = load(ctx, active) if active else None
    if s is not None and s["sender"] != sender:
        s = None  # a session belongs to the family member who started it
    if s is None or s["state"] in TERMINAL or RESTART.search(text):
        s = new_session(ctx, sender, str(ctx.session))
        await start(ctx, s)
        return
    expected = EXPECTED.get(s["state"], set())
    if not sel or sel.get("action") not in expected:
        ctx.logger.info(f"[{s['session_id']}] {s['state']}: unparseable or unexpected selection, re-sending card")
        await resend_card(ctx, s, "I didn't catch that. Please use the card below.")
        return
    await HANDLERS[s["state"]](ctx, s, sel)

"""Session state machine for rooted-orchestrator.

INTAKE -> STEADI_1 -> STEADI_2 -> RISK_RESULT -> PICK_BATHROOM -> RENDER -> QUOTES -> FUNDING
-> REVIEW -> CONSENT -> SPLIT -> PAY -> BOOKED -> BALANCE -> DONE (payments in billing.py),
plus the terminal LOW_RISK / CANCELLED / DECLINED.
Each state names the card the user is looking at; the matching selection moves to the next state.
"""
import asyncio
import base64
import json
import os
import re
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any
from urllib.parse import urlparse

from uagents import Context, Model
from uagents_core.contrib.protocols.chat import ChatMessage

from shared import cards, demo
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
TIMEOUTS = {"risk": 15, "visualizer": 40, "photo": 30, "funding": 15, "contractor": 15, "chart": 10}

PRICES = {p["id"]: p for p in json.loads((ROOT / "data" / "prices.json").read_text())["items"]}
BATHROOMS = [
    {"id": f"b{i}", "label": label, "image": f"assets/bathrooms/b{i}.png"}
    for i, label in enumerate(["Tub with shower", "Step-in shower", "Small bathroom", "Rug and clutter", "Dim bathroom"], 1)
]
CONTRACTORS = {c["id"]: c for c in json.loads((ROOT / "data" / "contractors.json").read_text())["contractors"]}
PROGRAMS = {p["id"]: p for p in json.loads((ROOT / "data" / "programs.json").read_text())["programs"]}
FULL_REMODEL_ITEMS = [p["id"] for p in PRICES.values() if p["scope"] == "full"]
NUMBER_WORDS = {1: "one", 2: "two"}
PHOTO_URL = re.compile(r"https://[^\s<>\"')\]]+", re.I)
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif")
BEFORE_PICKER = {"INTAKE", "STEADI_1", "STEADI_2", "RISK_RESULT"}
PHOTO_FAILED = "I couldn't process the photo, so let's pick the closest layout instead."
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
TERMINAL = {"LOW_RISK", "CANCELLED", "DECLINED", "WAITING_GRANT", "DONE"}
PAYING = {"PAY", "BOOKED", "BALANCE"}  # chat in these states goes to billing instead of the card flow


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
    ctx.logger.info(f"[{session_id}] new session for sender {sender}")
    return s


def transition(ctx: Context, s: dict, new_state: str) -> None:
    ctx.logger.info(f"[{s['session_id']}] {s['state']} -> {new_state}")
    s["state"] = new_state
    save(ctx, s)


# ---- selection parsing -----------------------------------------------------

def _truthy(v: Any) -> bool:
    return v is True or str(v).strip().lower() in ("true", "yes", "on", "1", "checked")


def _flatten(sel: dict) -> dict:
    # Forms may nest field values under one of these keys. CTA ids win over field values.
    for nested in ("values", "fields", "form", "form_values", "data"):
        if isinstance(sel.get(nested), dict):
            sel = {**_flatten(sel[nested]), **{k: v for k, v in sel.items() if k != nested}}
    return sel


def parse(text: str, allowed: set[str] | None = None) -> dict | None:
    # cards.parse_selection unwraps {"selection": ..., "approved": ...} and returns None when approved is false.
    sel = cards.parse_selection(text, allowed)
    return _flatten(sel) if sel else None


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(float(str(v).replace(",", "").replace("$", "").strip()))
    except (TypeError, ValueError):
        return default


# ---- outbound helpers ------------------------------------------------------

async def say(ctx: Context, s: dict, text: str) -> None:
    await ctx.send(s["sender"], text_message(text))


async def show(ctx: Context, s: dict, narration: str, card: tuple[str, dict], terminal: bool = False,
               drawer_width_px: int | None = None) -> None:
    kind, payload = card
    s["card"] = {"narration": narration, "kind": kind, "payload": payload, "terminal": terminal, "drawer_width_px": drawer_width_px}
    await ctx.send(s["sender"], cards.card_message(narration, kind, payload, terminal=terminal, drawer_width_px=drawer_width_px))


async def resend_card(ctx: Context, s: dict, note: str) -> None:
    c = s.get("card")
    if not c:
        await say(ctx, s, note)
        return
    await ctx.send(s["sender"], cards.card_message(f"{note}\n\n{c['narration']}".strip(), c["kind"], c["payload"],
                                                   terminal=c["terminal"], drawer_width_px=c.get("drawer_width_px")))


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


def marker_order(marked_ids: list[str], hazard_ids: list[str]) -> list[str]:
    """Fix list for the picture: "Fix n" is marker n, so the image's fixes come first in marker order.

    A hazard fix the picture doesn't show is kept after them, unless the picture already has a fix of the
    same kind (a shower grab bar stands in for a tub grab bar in a step-in shower).
    """
    marked = [f for f in marked_ids if f in PRICES]
    shown = {PRICES[f]["category"] for f in marked}
    return marked + [f for f in hazard_ids if f in PRICES and f not in marked and PRICES[f]["category"] not in shown]


def photo_url_in(text: str) -> str | None:
    """An attached photo: ASI:One forwards it as a res.cloudinary.com link inside the text, not as a resource."""
    for url in PHOTO_URL.findall(text or ""):
        url = url.rstrip(".,;:!?")
        parts = urlparse(url)
        if (parts.hostname or "").endswith("res.cloudinary.com") or parts.path.lower().endswith(IMAGE_EXTENSIONS):
            return url
    return None


def fix_list(item_ids: list[str]) -> list[dict]:
    return [{"id": i, "label": PRICES[i]["label"]} for i in item_ids if i in PRICES]


# ---- state handlers --------------------------------------------------------

async def start(ctx: Context, s: dict) -> None:
    await show(ctx, s, "Hi, I'm Rooted. I help families make an older parent's home safer so they can keep living there. "
                       "First, a few questions about your parent.",
               cards.intake_card(s["session_id"], defaults=demo.MARIA_INTAKE if demo.DEMO_MODE else None))
    transition(ctx, s, "INTAKE")


async def on_intake(ctx: Context, s: dict, sel: dict) -> None:
    sel = demo.with_defaults(sel, demo.MARIA_INTAKE)
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
    transition(ctx, s, "PICK_BATHROOM")
    if s["data"].get("photo_url"):
        await use_photo(ctx, s)
        return
    await show_picker(ctx, s, "Most falls at home happen in the bathroom, so let's start there.")


async def show_picker(ctx: Context, s: dict, narration: str) -> None:
    name = s["data"]["intake"]["parent_name"]
    await show(ctx, s, narration, cards.bathroom_pick_card(s["session_id"], BATHROOMS, name), drawer_width_px=cards.WIDE_DRAWER_PX)


def hazard_fix_ids(s: dict) -> list[str]:
    fix_ids: list[str] = []
    for h in s["data"]["risk"]["hazards"]:
        fix_ids += [f for f in h.get("fix_item_ids", []) if f in PRICES and f not in fix_ids]
    return fix_ids or ["grab_bar_tub", "grab_bar_toilet", "night_light"]


async def use_photo(ctx: Context, s: dict) -> None:
    """The family's own photo instead of the picker. Any failure falls back to the picker; nothing else changes."""
    sid = s["session_id"]
    fix_ids = hazard_fix_ids(s)
    await say(ctx, s, "Drawing your bathroom with these fixes...")
    render = await call(ctx, s, "visualizer", RenderRequest(session_id=sid, bathroom_id="photo", fix_item_ids=fix_ids,
                                                            photo_url=s["data"]["photo_url"]), RenderResult, TIMEOUTS["photo"])
    if render is None or render.error or not render.marked_url:
        s["data"].pop("photo_url", None)  # don't try the same photo again
        save(ctx, s)
        ctx.logger.info(f"[{sid}] photo path failed ({render.error if render else 'timeout'}), showing the picker")
        await show_picker(ctx, s, PHOTO_FAILED)
        return
    # ASI:One drops a card whose image is a data: URI, so the edited photo goes to Agentverse storage and is sent
    # as its own resource message (the image-generation example's pattern), then the fix list without an image.
    try:
        jpeg = base64.b64decode(render.marked_url.split(",", 1)[1])
        resource = await asyncio.to_thread(upload_photo, sid, jpeg, s["sender"])
    except Exception as exc:
        s["data"].pop("photo_url", None)
        save(ctx, s)
        ctx.logger.error(f"[{sid}] photo upload failed: {type(exc).__name__}, showing the picker")
        await show_picker(ctx, s, PHOTO_FAILED)
        return
    # No markers on a real photo, so the list keeps the line-item order.
    s["data"].update(bathroom_id="photo", fix_item_ids=fix_ids)
    save(ctx, s)
    await say(ctx, s, "Here is your bathroom with these fixes.")
    await ctx.send(s["sender"], resource)
    urgent = sum(i.unit_price_cents for i in line_items(fix_ids))
    full = urgent + sum(i.unit_price_cents for i in line_items(FULL_REMODEL_ITEMS))
    await show(ctx, s, "These are the fixes in the picture above.",
               cards.photo_fixes_card(sid, fix_list(fix_ids), urgent, full))
    transition(ctx, s, "RENDER")


STORAGE_URL = "https://agentverse.ai/v1/storage"


def upload_photo(session_id: str, jpeg: bytes, reader: str) -> ChatMessage:
    """Store the edited photo for 24 h, readable by this chat's sender only, and wrap it as a resource message."""
    from uagents_core.contrib.protocols.chat import Resource, ResourceContent
    from uagents_core.storage import ExternalStorage

    storage = ExternalStorage(api_token=os.environ["AGENTVERSE_API_KEY"], storage_url=STORAGE_URL)
    asset_id = storage.create_asset(name=f"rooted-{session_id}", content=jpeg, mime_type="image/jpeg", lifetime_hours=24)
    storage.set_permissions(asset_id=asset_id, agent_address=reader, read=True, write=False)
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=[ResourceContent(
        type="resource", resource_id=asset_id,
        resource=Resource(uri=f"agent-storage://{STORAGE_URL}/{asset_id}", metadata={"mime_type": "image/jpeg", "role": "generated-image"}),
    )])


async def on_pick_bathroom(ctx: Context, s: dict, sel: dict) -> None:
    sid = s["session_id"]
    bathroom_id = sel.get("bathroom_id") if sel.get("bathroom_id") in {b["id"] for b in BATHROOMS} else None
    if not bathroom_id:
        await resend_card(ctx, s, "Please pick one of the bathrooms.")
        return
    fix_ids = hazard_fix_ids(s)
    await say(ctx, s, "Drawing the safer version of this bathroom...")
    render = await call(ctx, s, "visualizer", RenderRequest(session_id=sid, bathroom_id=bathroom_id, fix_item_ids=fix_ids),
                        RenderResult, TIMEOUTS["visualizer"])
    marked = render.marked_url if render and not render.error else f"assets/bathrooms/{bathroom_id}.png"
    if render and not render.error:
        fix_ids = marker_order(render.fix_item_ids, fix_ids)
    s["data"].update(bathroom_id=bathroom_id, fix_item_ids=fix_ids)
    save(ctx, s)
    urgent = sum(i.unit_price_cents for i in line_items(fix_ids))
    full = urgent + sum(i.unit_price_cents for i in line_items(FULL_REMODEL_ITEMS))
    # A preset room, not theirs, so the title says what it is.
    title = f"What these fixes look like in a bathroom like {s['data']['intake']['parent_name']}'s"
    await show(ctx, s, "The numbers on the picture match the list of fixes.",
               cards.after_detail_card(sid, bathroom_id, marked, fix_list(fix_ids), urgent, full, title=title))
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
    quotes = await negotiate(ctx, s, quotes)
    s["data"]["quotes"] = {q.quote_id: q.dict() for q in quotes}
    await show(ctx, s, quotes_narration(missing, len(quotes)), cards.quotes_card(sid, quotes))
    transition(ctx, s, "QUOTES")


def quotes_narration(missing: list[str], answered: int) -> str:
    if not missing:
        return f"{answered} contractor{'s' if answered != 1 else ''} answered."
    return (f"{' and '.join(missing)} didn't respond within {TIMEOUTS['contractor']} s, "
            f"so I compared the other {NUMBER_WORDS.get(answered, answered)}.")


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
    # An alternative (PICF) replaces paying now instead of adding to it, so it has no bar or the steps wouldn't add up.
    stacked = [m for m in funding.matches if not m.alternative_to_pay_now]
    if stacked and not funding.chart_url:
        chart = await call(ctx, s, "visualizer", WaterfallRequest(
            session_id=sid, total_cents=funding.total_cents, family_share_cents=funding.family_share_cents,
            program_amounts=[ProgramAmount(name=m.name, amount_cents=m.amount_cents) for m in stacked],
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
        "deposit": cards.funding_summary(f, DEPOSIT_PERCENT)["deposit_cents"],
    }


async def on_accept_funding(ctx: Context, s: dict, sel: dict) -> None:
    if sel.get("choice") == "wait_picf":
        await on_wait_for_program(ctx, s, sel)
        return
    s["data"]["prepare_packet"] = sel.get("prepare_packet") == "yes"
    p = _plan(s)
    await show(ctx, s, "Please review the plan.", cards.plan_review_card(
        s["session_id"], p["quote"]["quote_id"], p["items"], p["quote"]["contractor_name"], p["quote"]["earliest_date"],
        s["data"]["funding"], DEPOSIT_PERCENT))
    transition(ctx, s, "REVIEW")


async def on_wait_for_program(ctx: Context, s: dict, sel: dict) -> None:
    """The family waits for an alternative program (PICF) instead of paying today: no payment, flow ends."""
    alts = [m for m in s["data"]["funding"].get("matches", []) if m.get("alternative_to_pay_now")]
    match = next((m for m in alts if m.get("program_id") == sel.get("program_id")), alts[0] if alts else None)
    program = PROGRAMS.get((match or {}).get("program_id") or sel.get("program_id", ""))
    if program is None:
        await resend_card(ctx, s, "That program isn't available for this plan. Please pick another option.")
        return
    items = [PRICES[i]["label"] for i in (match or {}).get("covered_item_ids", []) if i in PRICES]
    await show(ctx, s, f"Okay, nothing is charged. Here is what {program['name']} needs.",
               cards.wait_for_program_card(s["session_id"], program, items or [PRICES[i]["label"] for i in s["data"]["item_ids"] if i in PRICES]),
               terminal=True)
    transition(ctx, s, "WAITING_GRANT")


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
    await show(ctx, s, "Last step: who pays the deposit?", cards.split_card(
        s["session_id"], p["quote"]["quote_id"], s["data"]["funding"], DEPOSIT_PERCENT,
        defaults=demo.DEMO_SPLIT if demo.DEMO_MODE else None))
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
    sel = demo.with_defaults(sel, demo.DEMO_SPLIT)
    p = _plan(s)
    percents = [_int(x) for x in re.findall(r"\d+", str(sel.get("custom_percents") or ""))] if sel.get("split") == "custom" else None
    shares = split_shares(p["deposit"], _int(sel.get("payers"), 1), percents)
    contacts = [c.strip().lstrip("@") for c in str(sel.get("payer_contacts") or "").split(",") if c.strip().lstrip("@")]
    contacts = [c[:1].upper() + c[1:] for c in contacts]  # handles are lowercase; the cards show names
    names = ["You"] + [contacts[i] if i < len(contacts) else f"Payer {i + 2}" for i in range(len(shares) - 1)]
    s["data"]["plan_title"] = f"{s['data']['intake']['parent_name']}'s home safety plan"
    s["data"]["split_percents"] = percents if percents and len(percents) == len(shares) and sum(percents) == 100 else None
    from agents.orchestrator import billing

    await billing.start_deposits(ctx, s, shares, names)


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

FALLBACK_TEXT = "Sorry, something went wrong on my side. Please try that again, or say \"start over\"."


def log_non_text(ctx: Context, sender: str, msg: ChatMessage) -> None:
    """Shape of anything that isn't text (e.g. an image attachment): type, URL scheme and host, MIME type and
    metadata key names. Never the full URL, a file name or other values, which can identify the family."""
    for c in msg.content:
        kind = getattr(c, "type", type(c).__name__)
        if kind == "text":
            # ASI:One may turn an attachment into text, so report any link's host (never the link or the words).
            hosts = sorted({urlparse(u).hostname or "?" for u in re.findall(r"https?://\S+", c.text)})
            if hosts:
                ctx.logger.info(f"[{ctx.storage.get(_active_key(sender))}] text from {sender} has {len(c.text)} chars "
                                f"and links to {hosts}")
            continue
        detail = ""
        if kind == "resource":
            resources = c.resource if isinstance(c.resource, list) else [c.resource]
            detail = "; ".join(f"uri={urlparse(r.uri).scheme}://{urlparse(r.uri).hostname} "
                               f"mime={(r.metadata or {}).get('mime_type', '?')} meta_keys={sorted(r.metadata or {})}"
                               for r in resources)
        elif kind == "metadata":
            detail = f"keys={sorted(c.metadata)}"
        ctx.logger.info(f"[{ctx.storage.get(_active_key(sender))}] non-text content from {sender}: "
                        f"{kind} ({type(c).__name__}) {detail}".rstrip())


async def handle_chat(ctx: Context, sender: str, msg: ChatMessage) -> None:
    log_non_text(ctx, sender, msg)
    try:
        await _handle_chat(ctx, sender, msg)
    except Exception as exc:  # a crash must still leave the family with a reply and the card they were on
        ctx.logger.error(f"[{ctx.storage.get(_active_key(sender))}] chat handler failed: {type(exc).__name__}")
        active = ctx.storage.get(_active_key(sender))
        s = load(ctx, active) if active else None
        try:
            if s is not None and s.get("card") and s["state"] not in TERMINAL:
                await resend_card(ctx, s, FALLBACK_TEXT)
            else:
                await ctx.send(sender, text_message(FALLBACK_TEXT))
        except Exception as exc2:
            ctx.logger.error(f"fallback reply failed: {type(exc2).__name__}")


async def _handle_chat(ctx: Context, sender: str, msg: ChatMessage) -> None:
    from agents.orchestrator import billing

    text = message_text(msg)
    if m := billing.RESUME.match(text):
        await billing.resume(ctx, sender, m.group(1))
        return
    sel = parse(text)
    session_id = (sel or {}).get("session_id")
    s = load(ctx, session_id) if session_id else None
    if s is None:
        active = ctx.storage.get(_active_key(sender))
        s = load(ctx, active) if active else None
    if s is not None and s["sender"] != sender:
        s = None  # a session belongs to the family member who started it
    photo = photo_url_in(text)
    if s is None or s["state"] in TERMINAL or RESTART.search(text):
        s = new_session(ctx, sender, str(ctx.session))
        await start(ctx, s)
        if photo:
            s["data"]["photo_url"] = photo  # used once the flow reaches the bathroom step
            save(ctx, s)
            ctx.logger.info(f"[{s['session_id']}] photo received with the first message")
        return
    if s["state"] in PAYING:
        await billing.on_owner_text(ctx, s, sender, text)
        return
    if photo:
        s["data"]["photo_url"] = photo
        save(ctx, s)
        ctx.logger.info(f"[{s['session_id']}] photo received in {s['state']}")
        if s["state"] in ("PICK_BATHROOM", "RENDER"):
            transition(ctx, s, "PICK_BATHROOM")
            await use_photo(ctx, s)
            return
    expected = EXPECTED.get(s["state"], set())
    sel = parse(text, expected)  # now that the card is known, its own actions win in planner prose
    if photo and (not sel or sel.get("action") not in expected):
        note = ("Thanks for the photo. I'll use it when we get to the bathroom." if s["state"] in BEFORE_PICKER
                else "Thanks for the photo. This plan already has its picture; say \"start over\" to use the photo.")
        await resend_card(ctx, s, note)
        return
    if not sel or sel.get("action") not in expected:
        # Never the raw text: it can carry income or health answers.
        ctx.logger.info(f"[{s['session_id']}] {s['state']}: unexpected selection (parsed action={(sel or {}).get('action')}, "
                        f"json={text.lstrip().startswith('{')}, {len(text)} chars), re-sending card")
        await resend_card(ctx, s, "I didn't catch that. Please use the card below.")
        return
    await HANDLERS[s["state"]](ctx, s, sel)

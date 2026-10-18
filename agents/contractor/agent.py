"""contractor-*: one codebase started once per entry in data/contractors.json (CONTRACTOR_ID=juniper|mesa|arbor).

Fictional contractors priced from the market rates in data/prices.json times their multiplier.
Each answers QuoteRequest, accepts or rejects one CounterOffer per quote, and answers BookRequest.
CONTRACTOR_OFFLINE=mesa (comma-separated ids) makes that contractor stop answering, to demo failover.
"""
import json
import os
import secrets
from datetime import date, timedelta

from uagents import Context

from shared.models import BookConfirm, BookRequest, CounterOffer, Quote, QuoteRequest, QuotedItem, RevisedQuote
from shared.runtime import ROOT, chat_protocol, describe_only, make_agent

CONTRACTOR_ID = os.getenv("CONTRACTOR_ID", "juniper")
CONFIG = next(c for c in json.loads((ROOT / "data" / "contractors.json").read_text())["contractors"] if c["id"] == CONTRACTOR_ID)
PRICES = {p["id"]: p for p in json.loads((ROOT / "data" / "prices.json").read_text())["items"]}
ACCEPT_RATIO = 0.92  # lowest share of the original quote this contractor agrees to
QUOTE_VALID_DAYS = 7

agent = make_agent(CONTRACTOR_ID)
agent.include(
    chat_protocol(describe_only(f"I am {CONFIG['name']}, a fictional contractor that quotes home-safety fixes for rooted-orchestrator.")),
    publish_manifest=True,
)


def offline() -> bool:
    return CONTRACTOR_ID in {c.strip() for c in os.getenv("CONTRACTOR_OFFLINE", "").split(",")}


def item_price_cents(item_id: str, fallback_cents: int) -> int:
    market = PRICES[item_id]["unit_price_cents"] if item_id in PRICES else fallback_cents
    return round(market * CONFIG["multiplier"] / 100) * 100  # whole dollars


@agent.on_message(QuoteRequest, replies=Quote)
async def on_quote(ctx: Context, sender: str, req: QuoteRequest):
    if offline():
        ctx.logger.info(f"[{req.session_id}] QuoteRequest ignored: offline via CONTRACTOR_OFFLINE")
        return
    items = [
        QuotedItem(item_id=i.id, label=i.label, price_cents=item_price_cents(i.id, i.unit_price_cents) * i.quantity)
        for i in req.line_items
    ]
    today = date.today()
    quote = Quote(
        session_id=req.session_id,
        quote_id=f"q_{CONTRACTOR_ID}_{secrets.token_hex(3)}",
        contractor_id=CONTRACTOR_ID,
        contractor_name=CONFIG["name"],
        total_cents=sum(i.price_cents for i in items),
        items=items,
        earliest_date=(today + timedelta(days=CONFIG["lead_days"])).isoformat(),
        badges=CONFIG["badges"],
        expires_at=(today + timedelta(days=QUOTE_VALID_DAYS)).isoformat(),
        source_url=next((PRICES[i.item_id]["source_url"] for i in items if PRICES.get(i.item_id, {}).get("source_url")), ""),
    )
    ctx.storage.set(f"quote:{quote.quote_id}", {"total_cents": quote.total_cents, "countered": False})
    ctx.logger.info(f"[{req.session_id}] Quote {quote.quote_id}: {len(items)} items, total {quote.total_cents} cents")
    await ctx.send(sender, quote)


@agent.on_message(CounterOffer, replies=RevisedQuote)
async def on_counter(ctx: Context, sender: str, req: CounterOffer):
    if offline():
        ctx.logger.info(f"[{req.session_id}] CounterOffer ignored: offline via CONTRACTOR_OFFLINE")
        return
    stored = ctx.storage.get(f"quote:{req.quote_id}")
    if stored is None:
        await ctx.send(sender, RevisedQuote(session_id=req.session_id, quote_id=req.quote_id, accepted=False,
                                            total_cents=0, message="I don't have that quote."))
        return
    original = stored["total_cents"]
    if stored["countered"]:
        reply = RevisedQuote(session_id=req.session_id, quote_id=req.quote_id, accepted=False,
                             total_cents=stored.get("final_cents", original), message="That was already my best price.")
    elif req.target_cents >= original * ACCEPT_RATIO:
        reply = RevisedQuote(session_id=req.session_id, quote_id=req.quote_id, accepted=True,
                             total_cents=req.target_cents, message="Deal. I can do that price.")
    else:
        floor = round(original * ACCEPT_RATIO / 100) * 100
        reply = RevisedQuote(session_id=req.session_id, quote_id=req.quote_id, accepted=False,
                             total_cents=floor, message="I can't go that low, but this is my best and final price.")
    ctx.storage.set(f"quote:{req.quote_id}", {**stored, "countered": True, "final_cents": reply.total_cents})
    ctx.logger.info(f"[{req.session_id}] CounterOffer {req.quote_id}: accepted={reply.accepted} total {reply.total_cents} cents")
    await ctx.send(sender, reply)


@agent.on_message(BookRequest, replies=BookConfirm)
async def on_book(ctx: Context, sender: str, req: BookRequest):
    if offline():
        ctx.logger.info(f"[{req.session_id}] BookRequest ignored: offline via CONTRACTOR_OFFLINE")
        return
    booking_id = f"bk_{CONTRACTOR_ID}_{secrets.token_hex(3)}"
    ctx.logger.info(f"[{req.session_id}] Booked {req.quote_id} on {req.date} as {booking_id}")
    await ctx.send(
        sender,
        BookConfirm(session_id=req.session_id, quote_id=req.quote_id, booking_id=booking_id,
                    contractor_id=CONTRACTOR_ID, date=req.date, message=f"{CONFIG['name']} will see you on {req.date}."),
    )


if __name__ == "__main__":
    agent.run()

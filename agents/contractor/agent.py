"""contractor-*: one codebase started once per entry in data/contractors.json (CONTRACTOR_ID=juniper|mesa|arbor).

P1 stub with fixed sample pricing.
"""
import json
import os
from datetime import date, timedelta

from uagents import Context

from shared.models import BookConfirm, BookRequest, CounterOffer, Quote, QuoteRequest, QuotedItem, RevisedQuote
from shared.runtime import ROOT, chat_protocol, describe_only, make_agent

CONTRACTOR_ID = os.getenv("CONTRACTOR_ID", "juniper")
CONFIG = next(c for c in json.loads((ROOT / "data" / "contractors.json").read_text())["contractors"] if c["id"] == CONTRACTOR_ID)
STUB_BASE_CENTS = 30_000  # placeholder per item until P7 reads data/prices.json

agent = make_agent(CONTRACTOR_ID)
agent.include(
    chat_protocol(describe_only(f"I am {CONFIG['name']}, a fictional contractor that quotes home-safety fixes for rooted-orchestrator.")),
    publish_manifest=True,
)


@agent.on_message(QuoteRequest, replies=Quote)
async def on_quote(ctx: Context, sender: str, req: QuoteRequest):
    ctx.logger.info(f"[{req.session_id}] QuoteRequest with {len(req.line_items)} items, answering with stub quote")
    items = [
        QuotedItem(item_id=i.id, label=i.label, price_cents=round(STUB_BASE_CENTS * CONFIG["multiplier"]) * i.quantity)
        for i in req.line_items
    ]
    today = date.today()
    await ctx.send(
        sender,
        Quote(
            session_id=req.session_id,
            quote_id=f"q_{CONTRACTOR_ID}_{req.session_id[:8]}",
            contractor_id=CONTRACTOR_ID,
            contractor_name=CONFIG["name"],
            total_cents=sum(i.price_cents for i in items),
            items=items,
            earliest_date=(today + timedelta(days=CONFIG["lead_days"])).isoformat(),
            badges=CONFIG["badges"],
            expires_at=(today + timedelta(days=14)).isoformat(),
        ),
    )


@agent.on_message(CounterOffer, replies=RevisedQuote)
async def on_counter(ctx: Context, sender: str, req: CounterOffer):
    ctx.logger.info(f"[{req.session_id}] CounterOffer for {req.quote_id}, answering with stub acceptance")
    await ctx.send(
        sender,
        RevisedQuote(session_id=req.session_id, quote_id=req.quote_id, accepted=True, total_cents=req.target_cents, message="Stub: accepted."),
    )


@agent.on_message(BookRequest, replies=BookConfirm)
async def on_book(ctx: Context, sender: str, req: BookRequest):
    ctx.logger.info(f"[{req.session_id}] BookRequest for {req.quote_id} on {req.date}, answering with stub booking")
    await ctx.send(
        sender,
        BookConfirm(
            session_id=req.session_id,
            quote_id=req.quote_id,
            booking_id=f"bk_{CONTRACTOR_ID}_{req.session_id[:8]}",
            contractor_id=CONTRACTOR_ID,
            date=req.date,
        ),
    )


if __name__ == "__main__":
    agent.run()

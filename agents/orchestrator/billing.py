"""Deposit and balance collection for the person in this chat (payer 0) only.

States: SPLIT -> PAY (deposit) -> BOOKED -> "/complete" -> BALANCE -> DONE.
The split card computes every payer's share server-side, but only payer 0 gets a Stripe Checkout Session
and RequestPayment, sent to the sender of their latest message. Other shares stay "pending" (never shown
as paid); collecting them is out of scope for the demo. Booking fires once payer 0's share is paid.
Storage key checkout:<checkout_session_id> points back to the chat session.
"""
import asyncio
import re
from datetime import date, timedelta

from uagents import Context, Protocol
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from agents.orchestrator import flow, payments
from shared import cards
from shared.models import BookConfirm, BookRequest
from shared.runtime import text_message

RESEND = re.compile(r"\b(pay|resend|again|checkout|link)\b", re.I)
STUB_PAID = re.compile(r"^/stub-paid\s+(cs_stub_[0-9a-f]+)\s*$")
RETRY_TEXT = "Your payment isn't finished yet. Please finish checkout, then press Pay again."
KIND_BY_STATE = {"PAY": "deposit", "BALANCE": "balance"}


# ---- helpers ---------------------------------------------------------------

async def tell(ctx: Context, address: str, text: str) -> None:
    await ctx.send(address, text_message(text))


def _status_rows(s: dict, kind: str) -> list[dict]:
    return [{"name": p["name"], "amount_cents": p["charges"][kind]["amount_cents"], "paid": p["charges"][kind]["paid"]}
            for p in s["data"]["payers"] if kind in p["charges"]]


def _pending(s: dict, kind: str) -> list[dict]:
    return [{"name": p["name"], "amount_cents": p["charges"][kind]["amount_cents"]}
            for p in s["data"]["payers"][1:] if kind in p["charges"] and not p["charges"][kind]["paid"]]


async def show_status(ctx: Context, s: dict, kind: str, narration: str) -> None:
    title = "Deposit payments" if kind == "deposit" else "Balance payments"
    await flow.show(ctx, s, narration, cards.payment_status_card(s["session_id"], _status_rows(s, kind), title), terminal=True)


# ---- requesting money ------------------------------------------------------

async def request_charge(ctx: Context, s: dict, kind: str, address: str) -> None:
    """One Checkout Session + RequestPayment for payer 0's share, sent to `address` (their latest sender)."""
    sid = s["session_id"]
    charge = s["data"]["payers"][0]["charges"][kind]
    if charge["paid"]:
        return
    label = "30% deposit share" if kind == "deposit" else "balance share"
    try:
        stripe_meta = await asyncio.to_thread(
            payments.create_checkout, session_id=sid, payer_index=0, kind=kind, amount_cents=charge["amount_cents"],
            description=f"{label}: {s['data'].get('plan_title', 'home safety plan')}")
    except payments.PaymentError as e:
        ctx.logger.error(f"[{sid}] {kind}: {e}")
        await tell(ctx, address, "Sorry, I couldn't open the payment just now. Say \"pay\" in a minute and I'll try again.")
        return
    if charge.get("checkout_session_id"):
        ctx.storage.remove(f"checkout:{charge['checkout_session_id']}")
    charge["checkout_session_id"] = stripe_meta["checkout_session_id"]
    ctx.storage.set(f"checkout:{charge['checkout_session_id']}", {"session_id": sid, "kind": kind})
    flow.save(ctx, s)
    await ctx.send(address, RequestPayment(
        accepted_funds=[Funds(currency="USD", amount=f"{charge['amount_cents'] / 100:.2f}", payment_method="stripe")],
        recipient=str(ctx.agent.address),
        deadline_seconds=1800,
        reference=sid,
        description=f"Rooted {label}: {cards.usd(charge['amount_cents'])}",
        metadata={"stripe": stripe_meta, "service": "rooted_home_safety"},
    ))
    ctx.logger.info(f"[{sid}] RequestPayment {kind} checkout_session_id={charge['checkout_session_id']}")
    await tell(ctx, address, f"Your share is {cards.usd(charge['amount_cents'])}. Complete checkout, then press Pay.")


async def start_deposits(ctx: Context, s: dict, shares: list[int], names: list[str]) -> None:
    """Called from flow.on_split with server-side shares in cents; payer 0 is the person in this chat."""
    s["data"]["payers"] = [{"name": name, "charges": {"deposit": {"amount_cents": amount, "paid": False}}}
                           for name, amount in zip(names, shares)]
    flow.transition(ctx, s, "PAY")
    await show_status(ctx, s, "deposit", "Here is everyone's share of the deposit.")
    await request_charge(ctx, s, "deposit", s["sender"])


# ---- chat while paying -----------------------------------------------------

async def on_owner_text(ctx: Context, s: dict, sender: str, text: str) -> None:
    """The plan owner writes while in PAY, BOOKED or BALANCE."""
    if payments.STUB and (m := STUB_PAID.match(text.strip())):
        payments.stub_mark_paid(m.group(1))
        await flow.say(ctx, s, f"(stub) checkout {m.group(1)} marked paid")
        return
    if s["state"] == "BOOKED" and text.strip().lower().startswith("/complete"):
        await complete(ctx, s)
        return
    kind = KIND_BY_STATE.get(s["state"])
    if kind and RESEND.search(text):
        await request_charge(ctx, s, kind, sender)
        return
    if kind:
        await show_status(ctx, s, kind, "Here is where the payments stand. Say \"pay\" to get your checkout again.")
    else:
        await flow.say(ctx, s, "Your booking is confirmed. Say \"start over\" for a new plan.")


# ---- payment protocol (seller) --------------------------------------------

async def on_commit(ctx: Context, sender: str, msg: CommitPayment) -> None:
    ref = ctx.storage.get(f"checkout:{msg.transaction_id}") if msg.transaction_id else None
    if msg.funds.payment_method != "stripe" or ref is None:
        ctx.logger.warning(f"CommitPayment for unknown checkout {msg.transaction_id!r} ({msg.funds.payment_method})")
        await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id, reason="I couldn't find this payment. Say \"pay\" for a new checkout."))
        return
    s = flow.load(ctx, ref["session_id"])
    if s is None:
        await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id, reason="This plan has expired."))
        return
    sid, kind = s["session_id"], ref["kind"]
    charge = s["data"]["payers"][0]["charges"][kind]
    if charge["paid"]:
        await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
        return
    try:
        paid = await asyncio.to_thread(payments.is_paid, msg.transaction_id)
    except payments.PaymentError as e:
        ctx.logger.error(f"[{sid}] {kind}: {e}")
        await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id, reason="I couldn't confirm the payment with Stripe. Please press Pay again in a minute."))
        return
    if not paid:
        ctx.logger.info(f"[{sid}] {kind}: CommitPayment before checkout finished")
        await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id, reason=RETRY_TEXT))
        await tell(ctx, sender, RETRY_TEXT)
        return
    charge["paid"] = True
    flow.save(ctx, s)
    await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
    ctx.logger.info(f"[{sid}] {kind} paid")
    if kind == "deposit":
        await book(ctx, s)  # demo mode: book as soon as this chat's share is paid
        return
    pending = _pending(s, "balance")
    note = "".join(f" {cards.pending_line(p['name'])}" for p in pending)
    await show_status(ctx, s, "balance", f"Your balance share is paid. Thank you.{note}")
    flow.transition(ctx, s, "DONE")


async def on_reject(ctx: Context, sender: str, msg: RejectPayment) -> None:
    sid = ctx.storage.get(f"active:{sender}")
    s = flow.load(ctx, sid) if sid else None
    if s is None or s["state"] not in KIND_BY_STATE:
        return
    ctx.logger.info(f"[{s['session_id']}] RejectPayment in {s['state']}")
    await flow.say(ctx, s, "No problem, nothing was charged. Say \"pay\" any time to get the checkout again.")


def protocol() -> Protocol:
    proto = Protocol(spec=payment_protocol_spec, role="seller")

    @proto.on_message(CommitPayment)
    async def _commit(ctx: Context, sender: str, msg: CommitPayment):
        try:
            await on_commit(ctx, sender, msg)
        except Exception as exc:  # the payment drawer must never hang without an answer
            ctx.logger.error(f"CommitPayment {msg.transaction_id} failed: {type(exc).__name__}")
            await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id,
                                                 reason="Sorry, I couldn't confirm the payment. Please press Pay again in a minute."))

    @proto.on_message(RejectPayment)
    async def _reject(ctx: Context, sender: str, msg: RejectPayment):
        await on_reject(ctx, sender, msg)

    return proto


# ---- booking and completion ------------------------------------------------

async def book(ctx: Context, s: dict) -> None:
    sid = s["session_id"]
    quote = s["data"]["quotes"][s["data"]["quote_id"]]
    m = cards.funding_summary(s["data"]["funding"], flow.DEPOSIT_PERCENT)
    paid_cents = s["data"]["payers"][0]["charges"]["deposit"]["amount_cents"]
    when = quote.get("earliest_date") or (date.today() + timedelta(days=5)).isoformat()
    await flow.say(ctx, s, f"Your deposit share is in. Booking {quote['contractor_name']}...")
    confirm = await flow.call(ctx, s, quote["contractor_id"],
                              BookRequest(session_id=sid, quote_id=quote["quote_id"], date=when, deposit_percent=flow.DEPOSIT_PERCENT),
                              BookConfirm, flow.TIMEOUTS["contractor"])
    if confirm is None or not confirm.confirmed:
        booking_id = f"pending_{quote['contractor_id']}"
        await flow.say(ctx, s, f"Your deposit is safe. {quote['contractor_name']} hasn't confirmed yet; I'll keep {when} on hold.")
    else:
        booking_id = confirm.booking_id
        when = confirm.date or when
    s["data"]["booking"] = {"booking_id": booking_id, "date": when}
    await flow.show(ctx, s, "You're booked.", cards.booking_card(
        sid, booking_id, quote["contractor_name"], when, paid_cents, m["balance_cents"], _pending(s, "deposit")), terminal=True)
    flow.transition(ctx, s, "BOOKED")


async def complete(ctx: Context, s: dict) -> None:
    """Demo trigger: the install passed its completion check, so ask for this chat's share of the remaining 70%."""
    sid = s["session_id"]
    m = cards.funding_summary(s["data"]["funding"], flow.DEPOSIT_PERCENT)
    payers = s["data"]["payers"]
    # Same split rule as the deposit (even or the custom percentages), applied to the balance.
    shares = flow.split_shares(m["balance_cents"], len(payers), s["data"].get("split_percents"))
    for p, amount in zip(payers, shares):
        p["charges"]["balance"] = {"amount_cents": amount, "paid": False}
    booking = s["data"].get("booking", {})
    photo = f"assets/bathrooms/{s['data'].get('bathroom_id', 'b1')}_after.png"
    await flow.show(ctx, s, "The work is done and passed the completion check.",
                    cards.completion_card(sid, booking.get("booking_id", ""), photo, m["balance_cents"]), terminal=True)
    flow.transition(ctx, s, "BALANCE")
    await request_charge(ctx, s, "balance", s["sender"])

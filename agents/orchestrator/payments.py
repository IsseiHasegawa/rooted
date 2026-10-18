"""Stripe embedded Checkout for the Agent Payment Protocol (orchestrator is the seller).

ROOTED_PAYMENTS=stub swaps Stripe for an in-memory fake so tests/sim_client.py can run the payment
steps without a network or keys. Amounts always come from the server-side session, never from a message.
"""
import os
import secrets
import time

STRIPE_API_VERSION = "2025-03-31.basil"
CURRENCY = "usd"
STUB = os.getenv("ROOTED_PAYMENTS", "").strip().lower() == "stub"
_stub_paid: set[str] = set()


class PaymentError(Exception):
    """Any Stripe failure; the message is safe to log (no keys, no client secrets)."""


def _stripe():
    import stripe

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    # stripe 16 defaults to an API version that drops ui_mode="embedded"; the ASI:One checkout needs it.
    stripe.api_version = STRIPE_API_VERSION
    return stripe


def _return_url(session_id: str) -> str:
    base = (os.getenv("STRIPE_SUCCESS_URL") or "https://agentverse.ai/payment-success").strip()
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}session_id={{CHECKOUT_SESSION_ID}}&chat_session_id={session_id}"


def create_checkout(*, session_id: str, payer_index: int, kind: str, amount_cents: int, description: str) -> dict:
    """Returns metadata["stripe"] for RequestPayment: ui_mode, publishable_key, client_secret,
    id and checkout_session_id (the same value), currency, amount_cents (int)."""
    if amount_cents < 50:
        raise PaymentError(f"amount {amount_cents} is below Stripe's minimum charge")
    if STUB:
        cs_id = f"cs_stub_{secrets.token_hex(6)}"
        return {"ui_mode": "embedded", "publishable_key": "pk_stub", "client_secret": f"{cs_id}_secret_stub",
                "id": cs_id, "checkout_session_id": cs_id, "currency": CURRENCY, "amount_cents": amount_cents}
    stripe = _stripe()
    try:
        # Never pass payment_method_types. The horoscope sample, the only shape proven to render in ASI:One's
        # payment drawer, omits it and lets Stripe use the methods enabled in the dashboard. ui_mode="embedded"
        # plus return_url (redirect only if required) is also taken from that sample, with the API version pinned.
        session = stripe.checkout.Session.create(
            ui_mode="embedded",
            redirect_on_completion="if_required",
            mode="payment",
            return_url=_return_url(session_id),
            expires_at=int(time.time()) + 1800,
            line_items=[{
                "price_data": {
                    "currency": CURRENCY,
                    "product_data": {"name": "Rooted home safety plan", "description": description},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            metadata={"session_id": session_id, "payer_index": str(payer_index), "kind": kind},
        )
    except Exception as e:
        raise PaymentError(f"checkout create failed: {type(e).__name__}") from None
    # "id" duplicates checkout_session_id because the horoscope sample, whose checkout renders in ASI:One, sends both.
    return {"ui_mode": "embedded", "publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
            "client_secret": session.client_secret, "id": session.id, "checkout_session_id": session.id,
            "currency": CURRENCY, "amount_cents": amount_cents}


def is_paid(checkout_session_id: str) -> bool:
    if STUB:
        return checkout_session_id in _stub_paid
    stripe = _stripe()
    try:
        session = stripe.checkout.Session.retrieve(checkout_session_id)
    except Exception as e:
        raise PaymentError(f"checkout retrieve failed: {type(e).__name__}") from None
    return getattr(session, "payment_status", None) == "paid"


def stub_mark_paid(checkout_session_id: str) -> bool:
    """Stub mode only: the sim's stand-in for finishing Stripe checkout."""
    if not STUB:
        return False
    _stub_paid.add(checkout_session_id)
    return True

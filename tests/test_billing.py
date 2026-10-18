"""Payment requests: the RequestPayment shape proven in ASI:One, and the personal-assistant proxy case."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uagents_core.contrib.protocols.chat import ChatMessage  # noqa: E402
from uagents_core.contrib.protocols.payment import RequestPayment  # noqa: E402

from agents.orchestrator import billing, flow, payments  # noqa: E402
from shared.runtime import message_text, text_message  # noqa: E402

SID = "696586e1-74f5-4ba7-89a2-592ffcffa0e5"
PROXY = "agent1qdc626uuu220xcxad92dpj2ujh76vsyxg2lqs28qy097k5da22kas2xhu6l"  # personal-assistant proxy
ASI_ONE = "agent1qd7wlq3nwrhytvnrp7cck4ks5wkne76kwvg276n9mljhy7vxywuhsaplp5g"  # where the horoscope checkout rendered


class Ctx:
    def __init__(self, chat_session: str):
        self.store: dict = {}
        self.storage = SimpleNamespace(get=self.store.get, set=self.store.__setitem__, remove=lambda k: self.store.pop(k, None))
        self.logger = mock.Mock()
        self.session = chat_session
        self.agent = SimpleNamespace(address="agent1qrooted")
        self.sent: list[tuple[str, object]] = []

    async def send(self, to, msg):
        self.sent.append((to, msg))


def paying_session(ctx: Ctx, sender: str) -> dict:
    s = {"session_id": SID, "sender": sender, "state": "PAY", "card": None,
         "data": {"payers": [{"name": "You", "charges": {"deposit": {"amount_cents": 5_760, "paid": False}}},
                             {"name": "Leo", "charges": {"deposit": {"amount_cents": 5_760, "paid": False}}}]}}
    flow.save(ctx, s)
    ctx.store[flow._active_key(sender)] = SID
    return s


def almanac(kind: str):
    resp = mock.Mock(status_code=200)
    resp.json.return_value = {"type": kind}
    return resp


@pytest.fixture
def stripe_stub(monkeypatch):
    monkeypatch.setattr(payments, "STUB", True)
    billing._almanac_cache.clear()


def test_request_payment_matches_the_horoscope_shape(stripe_stub):
    ctx = Ctx("chat-session-now")
    s = paying_session(ctx, ASI_ONE)
    asyncio.run(billing.request_charge(ctx, s, "deposit", ASI_ONE))
    (to, req), (_, after) = ctx.sent
    assert to == ASI_ONE and isinstance(req, RequestPayment)
    stripe = req.metadata["stripe"]
    assert stripe["id"] == stripe["checkout_session_id"]
    # The protocol model types metadata values as str, so the int from payments.create_checkout is sent as "5760",
    # exactly as the horoscope sample's 100 went out as "100".
    assert stripe["amount_cents"] == "5760"
    assert {"ui_mode", "publishable_key", "client_secret", "currency"} <= set(stripe)
    assert req.reference == "chat-session-now"  # this chat's session, as in the sample
    assert req.deadline_seconds == 300
    assert [f.dict() for f in req.accepted_funds] == [{"amount": "57.60", "currency": "USD", "payment_method": "stripe"}]
    # Only text follows the request; the pending share is named there.
    assert isinstance(after, ChatMessage) and "Leo's share is pending" in message_text(after)


def test_start_deposits_sends_no_card_before_the_payment_request(stripe_stub):
    ctx = Ctx("chat")
    s = paying_session(ctx, ASI_ONE)
    s["state"] = "SPLIT"
    asyncio.run(billing.start_deposits(ctx, s, [5_760, 5_760], ["You", "Leo"]))
    assert isinstance(ctx.sent[0][1], RequestPayment)


def test_proxy_sender_gets_a_resume_code_and_another_chat_pays(monkeypatch):
    monkeypatch.setattr(payments, "STUB", False)
    billing._almanac_cache.clear()
    ctx = Ctx("proxy-chat")
    s = paying_session(ctx, PROXY)
    lookups = {PROXY: almanac("proxy"), ASI_ONE: almanac("custom")}
    with mock.patch.object(billing.requests, "get", side_effect=lambda url, timeout: lookups[url.rsplit("/", 1)[1]]), \
         mock.patch.object(payments, "create_checkout", return_value={
             "ui_mode": "embedded", "publishable_key": "pk", "client_secret": "cs_x_secret", "id": "cs_x",
             "checkout_session_id": "cs_x", "currency": "usd", "amount_cents": 5_760}):
        asyncio.run(billing.request_charge(ctx, s, "deposit", PROXY))
        assert not any(isinstance(m, RequestPayment) for _, m in ctx.sent)
        text = message_text(ctx.sent[-1][1])
        code = s["data"]["resume_code"]
        assert f"pay {code}" in text and "asi1.ai" in text
        # The code from a chat that can pay moves the plan's payments there.
        ctx.sent.clear()
        ctx.session = "asi-one-chat"
        asyncio.run(flow.handle_chat(ctx, ASI_ONE, text_message(f"pay {code.upper()}")))
    to, req = ctx.sent[0]
    assert to == ASI_ONE and isinstance(req, RequestPayment) and req.reference == "asi-one-chat"
    assert flow.load(ctx, SID)["sender"] == ASI_ONE


def test_unknown_resume_code(stripe_stub):
    ctx = Ctx("chat")
    asyncio.run(flow.handle_chat(ctx, ASI_ONE, text_message("pay abc123")))
    assert "couldn't find a payment" in message_text(ctx.sent[-1][1])


def test_almanac_failure_does_not_block_payment(monkeypatch):
    monkeypatch.setattr(payments, "STUB", False)
    billing._almanac_cache.clear()
    with mock.patch.object(billing.requests, "get", side_effect=billing.requests.ConnectionError()):
        assert billing.sender_can_pay(ASI_ONE) is True
    with mock.patch.object(billing.requests, "get", return_value=almanac("proxy")):
        assert billing.sender_can_pay(PROXY) is False

"""ASI:One Interactive Card builders and selection parsing.

One builder per screen in the plan's card table. Every builder returns (card_kind, payload) so the
orchestrator can call card_message(narration, *builder(...)). Every CTA selection carries action,
session_id and every id the next step needs, because the planner only narrates what is in the selection.
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent

ROOT = Path(__file__).resolve().parent.parent
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_DEPTH = 8

STEADI_PATH = ROOT / "data" / "steadi.json"
STEADI_LABEL = "Based on CDC STEADI"

# Card Playground check #5: public https images render, so every card image is a public URL. Relative refs
# (e.g. "bathrooms/b1.png") resolve against ROOTED_IMAGE_BASE_URL, the visualizer's asset host.
IMAGE_BASE_URL = os.getenv("ROOTED_IMAGE_BASE_URL", "https://raw.githubusercontent.com/rooted-lahacks/rooted/main/assets")
_ABSOLUTE_IMAGE = ("https://", "http://", "data:")

ACTIONS = (
    "submit_intake", "submit_steadi", "continue", "pick_bathroom", "get_quotes", "pick_quote",
    "accept_funding", "approve", "cancel", "consent", "decline", "submit_split",
)

INSURANCE_OPTIONS = [
    {"value": "none", "label": "Original Medicare / no plan"},
    {"value": "sample_ma_plan_a", "label": "Sample plan A (Medicare Advantage)"},
    {"value": "sample_ma_plan_b", "label": "Sample plan B (Medicare Advantage)"},
]

LOW_RISK_TIPS = [
    "Add night lights on the path from the bedroom to the bathroom.",
    "Remove throw rugs or secure them with double-sided tape.",
    "Keep often-used items within easy reach, without a step stool.",
    "Ask the doctor or pharmacist to review medicines once a year.",
]


# ---- message envelope ------------------------------------------------------

def card_message(narration: str, card_kind: str, payload: dict, *, terminal: bool = False) -> ChatMessage:
    content = []
    if narration:
        content.append(TextContent(type="text", text=narration))
    content.append(MetadataContent(type="metadata", metadata=card_metadata(card_kind, payload, terminal=terminal)))
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=content)


def card_metadata(card_kind: str, payload: dict, *, terminal: bool = False) -> dict[str, str]:
    metadata = {
        "card_protocol_version": "1",
        "requires_card_interaction": "true",
        "card_kind": card_kind,
        "card_payload": json.dumps(payload, separators=(",", ":")),
    }
    if terminal:
        metadata["is_terminal"] = "true"
    return metadata


def json_depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((json_depth(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((json_depth(v) for v in value), default=0)
    return 0


def element_depth(element: dict) -> int:
    """Custom element-tree depth as ASI:One counts it: the root element is level 1."""
    children = list(element.get("children", []))
    for item in element.get("items", []):
        children.extend(item.get("children", []))
    return 1 + max((element_depth(c) for c in children), default=0)


def validate_payload(card_kind: str, payload: dict) -> None:
    size = len(json.dumps(payload, separators=(",", ":")).encode())
    if size > MAX_PAYLOAD_BYTES:
        raise ValueError(f"{card_kind} payload is {size} bytes, limit {MAX_PAYLOAD_BYTES}")
    # The docs count element-tree depth; we also keep raw JSON depth within 8 in case the validator counts that.
    if json_depth(payload) > MAX_DEPTH or (card_kind == "custom" and element_depth(payload["root"]) > MAX_DEPTH):
        raise ValueError(f"{card_kind} payload nests deeper than {MAX_DEPTH}")


# ---- small helpers ---------------------------------------------------------

def image_src(ref: str) -> str:
    if not ref or ref.startswith(_ABSOLUTE_IMAGE):
        return ref
    name = ref if "." in ref.rsplit("/", 1)[-1] else f"{ref}.png"
    return f"{IMAGE_BASE_URL.rstrip('/')}/{name.lstrip('/')}"


def usd(cents: int) -> str:
    dollars = cents / 100
    return f"${dollars:,.0f}" if cents % 100 == 0 else f"${dollars:,.2f}"


def _as_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    return obj.dict() if hasattr(obj, "dict") else dict(obj)


def _sel(action: str, session_id: str, **ids: Any) -> dict:
    return {"action": action, "session_id": session_id, **{k: v for k, v in ids.items() if v is not None}}


def _source_text(label: str, url: str) -> dict:
    return {"type": "text", "value": f"Source: {label} ({url})", "style": "muted"}


def load_steadi() -> dict:
    return json.loads(STEADI_PATH.read_text())


# ---- 1. intake (form) ------------------------------------------------------

def intake_card(session_id: str, insurance_options: list[dict] | None = None) -> tuple[str, dict]:
    return "form", {
        "title": "About your parent",
        "fields": [
            {"name": "parent_name", "kind": "text", "label": "Parent's first name", "required": True, "placeholder": "Maria"},
            {"name": "age", "kind": "number", "label": "Age", "required": True},
            {"name": "community", "kind": "text", "label": "City or community", "required": True, "placeholder": "East Los Angeles"},
            {"name": "zip_code", "kind": "text", "label": "ZIP code", "required": True, "placeholder": "90022"},
            {"name": "tenure", "kind": "select", "label": "Owner or renter", "required": True,
             "options": [{"value": "owner", "label": "Owns the home"}, {"value": "renter", "label": "Rents"}]},
            {"name": "months_owned", "kind": "number", "label": "Months owned (owners; 2 years = 24)"},
            {"name": "household_size", "kind": "number", "label": "People in the household", "required": True},
            {"name": "annual_income", "kind": "number", "label": "Household income per year (USD, approximate)", "required": True},
            {"name": "insurance_plan", "kind": "select", "label": "Health plan", "required": True,
             "options": insurance_options or INSURANCE_OPTIONS},
            {"name": "concern", "kind": "text", "label": "What worries you most at home?", "placeholder": "Slippery tub, dark hallway at night"},
        ],
        "submit_cta": {"label": "Continue", "selection": _sel("submit_intake", session_id)},
    }


# ---- 2. STEADI (form, two cards of 6) --------------------------------------

def steadi_card(session_id: str, part: int, steadi: dict | None = None) -> tuple[str, dict]:
    steadi = steadi or load_steadi()
    questions = steadi["questions"][(part - 1) * 6: part * 6]
    return "form", {
        "title": f"Fall-risk check ({part} of 2)",
        "subtitle": f"Tick each statement that is true for your parent. {STEADI_LABEL} Stay Independent.",
        "fields": [{"name": q["id"], "kind": "checkbox", "label": q["text"]} for q in questions],
        "submit_cta": {"label": "Next" if part == 1 else "See the result",
                       "selection": _sel("submit_steadi", session_id, part=str(part))},
    }


# ---- 3. risk result (custom) -----------------------------------------------

def risk_result_card(session_id: str, score: int, at_risk: bool, hazards: list, source_url: str,
                     max_score: int = 14, fell_past_year: bool = False) -> tuple[str, dict]:
    hazards = [_as_dict(h) for h in hazards][:3]
    if not at_risk:
        return low_risk_card(session_id, score, source_url, max_score)
    reason = "a fall in the past year" if fell_past_year and score < 4 else f"a score of {score} (4 or more means at risk)"
    return "custom", {"root": {"type": "section", "title": "Fall-risk result", "children": [
        {"type": "group", "direction": "row", "gap": 8, "children": [
            {"type": "badge", "label": "High risk", "variant": "warning"},
            {"type": "badge", "label": f"Score {score} of {max_score}", "variant": "info"},
        ]},
        {"type": "text", "value": f"At risk of falling because of {reason}. Home changes lower fall rates for people at high risk."},
        {"type": "heading", "value": "Top hazards to fix", "level": 3},
        {"type": "list", "items": [
            {"children": [{"type": "text", "value": f"{i}. {h['label']}", "style": "emphasis"},
                          {"type": "text", "value": h.get("room", "").replace("_", " "), "style": "muted"}]}
            for i, h in enumerate(hazards, 1)
        ]},
        _source_text(STEADI_LABEL, source_url),
        {"type": "button", "label": "Find the fixes", "primary": True,
         "action": {"selection": _sel("continue", session_id, hazard_ids=",".join(h["id"] for h in hazards))}},
    ]}}


def low_risk_card(session_id: str, score: int, source_url: str, max_score: int = 14) -> tuple[str, dict]:
    """Terminal: no remodel is recommended, so send with card_message(..., terminal=True)."""
    return "custom", {"root": {"type": "section", "title": "Fall-risk result", "children": [
        {"type": "group", "direction": "row", "gap": 8, "children": [
            {"type": "badge", "label": "Lower risk", "variant": "success"},
            {"type": "badge", "label": f"Score {score} of {max_score}", "variant": "info"},
        ]},
        {"type": "text", "value": "No remodel needed right now. A few simple changes still help:"},
        {"type": "list", "items": [{"children": [{"type": "text", "value": tip}]} for tip in LOW_RISK_TIPS]},
        {"type": "text", "value": "Check again every year, or after any fall.", "style": "muted"},
        _source_text(STEADI_LABEL, source_url),
    ]}}


# ---- 4. bathroom picker (custom, choice_grid) -------------------------------

def bathroom_pick_card(session_id: str, bathrooms: list[dict], parent_name: str = "your parent") -> tuple[str, dict]:
    return "custom", {"root": {"type": "section", "title": f"Which bathroom looks most like {parent_name}'s?",
                               "subtitle": "Pick the closest one. It does not need to match exactly.", "children": [
        {"type": "choice_grid", "name": "bathroom_id", "choices": [
            {"value": b["id"], "label": b["label"], "image": image_src(b.get("image") or b["id"])} for b in bathrooms
        ]},
        {"type": "button", "label": "Use this bathroom", "primary": True,
         "action": {"selection": _sel("pick_bathroom", session_id)}},
    ]}}


# ---- 5. after image + fixes (detail) ----------------------------------------

def after_detail_card(session_id: str, bathroom_id: str, marked_url: str, fixes: list,
                      urgent_estimate_cents: int | None = None, full_estimate_cents: int | None = None) -> tuple[str, dict]:
    """fixes: [{id, label}] in marker order; marker numbers on the image follow this order."""
    fixes = [_as_dict(f) for f in fixes]
    return "detail", {
        "title": "Your bathroom, made safer",
        "hero_image": image_src(marked_url),
        "summary_rows": [{"label": f"Fix {i}", "value": f["label"]} for i, f in enumerate(fixes, 1)],
        "sub_options": {"name": "scope", "kind": "radio", "label": "What should contractors quote?", "choices": [
            {"value": "urgent", "label": "Urgent safety fixes only",
             "secondary_text": f"about {usd(urgent_estimate_cents)}" if urgent_estimate_cents else "fastest"},
            {"value": "full", "label": "Full bathroom remodel too",
             "secondary_text": f"about {usd(full_estimate_cents)}" if full_estimate_cents else "tub to walk-in shower"},
        ]},
        "ctas": [{"label": "Get 3 quotes", "primary": True,
                  "selection": _sel("get_quotes", session_id, bathroom_id=bathroom_id,
                                    fix_item_ids=",".join(f["id"] for f in fixes))}],
    }


# ---- 6. quotes (carousel) --------------------------------------------------

def quotes_carousel_card(session_id: str, quotes: list, image_by_contractor: dict[str, str] | None = None) -> tuple[str, dict]:
    quotes = [_as_dict(q) for q in quotes]
    cheapest = min((q["total_cents"] for q in quotes), default=None)
    earliest = min((q["earliest_date"] for q in quotes), default=None)
    items = []
    for q in quotes:
        badges = [{"label": b, "variant": "success" if "CAPS" in b else "info"} for b in q.get("badges", [])]
        labels = {b["label"] for b in badges}
        if q["total_cents"] == cheapest and "Lowest price" not in labels:
            badges.append({"label": "Lowest price", "variant": "success"})
        if q["earliest_date"] == earliest and not any(lbl.startswith("Earliest") for lbl in labels):
            badges.append({"label": f"Earliest: {q['earliest_date']}", "variant": "info"})
        item = {
            "id": q["quote_id"],
            "title": q["contractor_name"],
            "subtitle": f"{len(q.get('items', []))} items · start {q['earliest_date']}",
            "badges": badges,
            "secondary_text": f"{usd(q['total_cents'])} · valid until {q['expires_at']}",
            "primary_cta": {"label": "Pick this quote",
                            "selection": _sel("pick_quote", session_id, quote_id=q["quote_id"], contractor_id=q["contractor_id"])},
        }
        image = (image_by_contractor or {}).get(q["contractor_id"])
        if image:
            item["image"] = image_src(image)
        items.append(item)
    return "carousel", {
        "title": f"{len(quotes)} contractor quote{'s' if len(quotes) != 1 else ''}",
        "subtitle": "Priced from national market rates. We asked each one for a better price once.",
        "items": items,
    }


# ---- 7. funding (custom) ---------------------------------------------------

def funding_card(session_id: str, quote_id: str, funding: Any) -> tuple[str, dict]:
    f = _as_dict(funding)
    matches = [_as_dict(m) for m in f.get("matches", [])]
    children: list[dict] = []
    if f.get("chart_url"):
        children.append({"type": "image", "src": image_src(f["chart_url"]), "alt": "Cost waterfall", "aspect_ratio": "16:9"})
    children += [
        {"type": "group", "direction": "row", "gap": 8, "children": [
            {"type": "badge", "label": f"Total {usd(f.get('total_cents', 0))}", "variant": "info"},
            {"type": "badge", "label": f"Covered {usd(f.get('covered_cents', 0))}", "variant": "success"},
            {"type": "badge", "label": f"Family share {usd(f.get('family_share_cents', 0))}", "variant": "warning"},
        ]},
        {"type": "heading", "value": "Programs that match" if matches else "No program matched", "level": 3},
    ]
    if matches:
        children.append({"type": "list", "items": [
            {"children": [
                {"type": "text", "value": m["name"], "style": "emphasis"},
                {"type": "badge", "label": usd(m["amount_cents"]), "variant": "success"},
                {"type": "badge", "label": "After application" if m.get("funded_later") else "Pay now", "variant": "warning" if m.get("funded_later") else "info"},
                {"type": "text", "value": m.get("next_step") or "", "style": "body"},
                {"type": "text", "value": f"Source: {m['source_url']} (as of {m['as_of']})", "style": "muted"},
            ]} for m in matches
        ]})
    children.append({"type": "text", "value": "Eligibility is checked with fixed rules from each program's official page.", "style": "muted"})
    ids = {"quote_id": quote_id}
    children.append({"type": "group", "direction": "row", "gap": 8, "children": [
        {"type": "button", "label": "Continue", "primary": True,
         "action": {"selection": _sel("accept_funding", session_id, prepare_packet="no", **ids)}},
        {"type": "button", "label": "Continue and prepare the grant packet",
         "action": {"selection": _sel("accept_funding", session_id, prepare_packet="yes", **ids)}},
    ]})
    return "custom", {"root": {"type": "section", "title": "Help paying for it", "children": children}}


# ---- 8. plan review (review) -----------------------------------------------

def plan_review_card(session_id: str, quote_id: str, items: list[str], contractor_name: str, date: str,
                     total_cents: int, covered_cents: int, family_share_cents: int, deposit_percent: int = 30) -> tuple[str, dict]:
    deposit = family_share_cents * deposit_percent // 100
    ids = {"quote_id": quote_id}
    return "review", {
        "title": "Review the plan",
        "summary_rows": [
            {"label": "Fixes", "value": ", ".join(items)},
            {"label": "Contractor", "value": contractor_name},
            {"label": "Install date", "value": date},
            {"label": "Total", "value": usd(total_cents)},
            {"label": "Covered by programs", "value": usd(covered_cents)},
            {"label": "Family share", "value": usd(family_share_cents)},
            {"label": f"Deposit today ({deposit_percent}%)", "value": usd(deposit)},
            {"label": "Balance", "value": f"{usd(family_share_cents - deposit)} after the work passes a completion check"},
        ],
        "approve_cta": {"label": "Approve plan", "primary": True, "selection": _sel("approve", session_id, **ids)},
        "reject_cta": {"label": "Cancel", "selection": _sel("cancel", session_id, **ids)},
    }


# ---- 9. elder consent (review) ---------------------------------------------

def consent_card(session_id: str, quote_id: str, parent_name: str, items: list[str], date: str) -> tuple[str, dict]:
    ids = {"quote_id": quote_id}
    return "review", {
        "title": f"{parent_name} has reviewed this plan",
        "summary_rows": [
            {"label": "What changes", "value": ", ".join(items)},
            {"label": "When", "value": date},
            {"label": "Who decides", "value": f"{parent_name}. Nothing is booked without this consent."},
        ],
        "approve_cta": {"label": f"{parent_name} agrees", "primary": True, "selection": _sel("consent", session_id, **ids)},
        "reject_cta": {"label": "Not yet", "selection": _sel("decline", session_id, **ids)},
    }


# ---- 10. family split (form) -----------------------------------------------

def split_card(session_id: str, quote_id: str, deposit_cents: int) -> tuple[str, dict]:
    return "form", {
        "title": f"Split the {usd(deposit_cents)} deposit",
        "fields": [
            {"name": "payers", "kind": "number", "label": "How many family members will pay?", "required": True},
            {"name": "split", "kind": "select", "label": "How to split", "required": True,
             "options": [{"value": "even", "label": "Evenly"}, {"value": "custom", "label": "Custom percentages"}]},
            {"name": "custom_percents", "kind": "text", "label": "Custom percentages, in payer order", "placeholder": "50, 25, 25"},
            {"name": "payer_contacts", "kind": "text", "label": "Other payers (ASI:One handles, comma separated)", "placeholder": "@leo, @ana"},
        ],
        "submit_cta": {"label": "Send payment requests", "selection": _sel("submit_split", session_id, quote_id=quote_id)},
    }


# ---- 11. payment (Stripe via Agent Payment Protocol) -----------------------

def stripe_metadata(publishable_key: str, client_secret: str, checkout_session_id: str,
                    amount_cents: int, currency: str = "usd") -> dict:
    """metadata["stripe"] for RequestPayment. The publishable key is public; the secret key never goes here."""
    return {
        "ui_mode": "embedded",
        "publishable_key": publishable_key,
        "client_secret": client_secret,
        "checkout_session_id": checkout_session_id,
        "currency": currency,
        "amount_cents": amount_cents,
    }


def payment_status_card(session_id: str, payers: list[dict]) -> tuple[str, dict]:
    """Informational (terminal): payers = [{name, amount_cents, paid}]. Payment itself is RequestPayment."""
    paid = sum(1 for p in payers if p.get("paid"))
    return "custom", {"root": {"type": "section", "title": "Deposit payments",
                               "subtitle": f"{paid} of {len(payers)} paid", "children": [
        {"type": "list", "items": [
            {"children": [
                {"type": "text", "value": f"{p['name']}: {usd(p['amount_cents'])}"},
                {"type": "badge", "label": "Paid" if p.get("paid") else "Waiting", "variant": "success" if p.get("paid") else "warning"},
            ]} for p in payers
        ]},
        {"type": "text", "value": "Each payer gets a secure Stripe checkout in their own chat.", "style": "muted"},
    ]}}


# ---- 12. booking confirmed (custom, terminal) ------------------------------

def booking_card(session_id: str, booking_id: str, contractor_name: str, date: str,
                 deposit_cents: int, balance_cents: int) -> tuple[str, dict]:
    steps = [
        ("Deposit paid", usd(deposit_cents), "success"),
        (f"Install on {date}", contractor_name, "info"),
        ("Completion check", "photos of the finished work", "info"),
        ("Balance", f"{usd(balance_cents)} after the check passes", "warning"),
    ]
    return "custom", {"root": {"type": "section", "title": "Booked", "subtitle": f"Booking {booking_id}", "children": [
        {"type": "list", "items": [
            {"children": [
                {"type": "badge", "label": str(i), "variant": variant},
                {"type": "text", "value": label, "style": "emphasis"},
                {"type": "text", "value": detail, "style": "muted"},
            ]} for i, (label, detail, variant) in enumerate(steps, 1)
        ]},
        {"type": "text", "value": "The plan is ready to share with an occupational therapist.", "style": "muted"},
    ]}}


def completion_card(session_id: str, booking_id: str, photo_url: str, balance_cents: int) -> tuple[str, dict]:
    """Informational (terminal): the balance itself is a RequestPayment sent next to this card."""
    return "custom", {"root": {"type": "section", "title": "Work completed", "subtitle": f"Booking {booking_id}", "children": [
        {"type": "image", "src": image_src(photo_url), "alt": "Finished bathroom", "aspect_ratio": "16:9"},
        {"type": "badge", "label": "Completion check passed", "variant": "success"},
        {"type": "text", "value": f"The remaining {usd(balance_cents)} is now due."},
    ]}}


# ---- selection parsing -----------------------------------------------------

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_CONTRACTORS = ("juniper", "mesa", "arbor")
# Prose phrasings the planner may use instead of the raw action name; checked in order.
_ACTION_PHRASES: list[tuple[str, str]] = [
    ("submit_intake", r"submit_intake|intake"),
    ("submit_steadi", r"submit_steadi|steadi|fall[- ]risk (check|questions)"),
    ("submit_split", r"submit_split|split"),
    ("pick_bathroom", r"pick_bathroom|bathroom_id|\bbathroom b\d+"),
    ("get_quotes", r"get_quotes|get (3 |three )?quotes"),
    ("pick_quote", r"picked (the )?quote|chose (the )?quote"),
    ("accept_funding", r"accept_funding|prepare_packet"),
    ("decline", r"\bdecline[ds]?\b|not yet|does not (agree|consent)"),
    ("consent", r"\bconsent(s|ed)?\b|\bagrees?\b"),
    ("cancel", r"\bcancel(s|led|ed)?\b"),
    ("approve", r"\bapprove[ds]?\b"),
    ("continue", r"\bcontinue[ds]?\b|find the fixes"),
    ("pick_quote", r"\bq_[a-z0-9_]+"),
]


def parse_selection(text: str | dict) -> dict | None:
    """Returns the flat selection dict ({"action": ..., "session_id": ..., ...}) or None.

    Accepted forms, tried in order:
    1. JSON wrapper {"selection": {...}, "approved": true}, as the Card Playground delivers a CTA result.
       Extra keys beside "selection" (other than "approved") are merged in, with the selection winning.
       approved=false means the user dismissed the card without choosing, so it returns None and the
       orchestrator re-sends the current card.
    2. Flat JSON {"action": ..., ...} from a direct @mention.
    3. Prose narrated by the ASI:One planner, parsed with regexes.
    """
    data = text if isinstance(text, dict) else None
    if data is None:
        text = (text or "").strip()
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    if isinstance(data, dict):
        if isinstance(data.get("selection"), dict):
            if data.get("approved") is False or str(data.get("approved")).lower() == "false":
                return None
            extra = {k: v for k, v in data.items() if k not in ("selection", "approved")}
            data = {**extra, **data["selection"]}
        if "action" in data:
            return data
        text = json.dumps(data)
    elif not isinstance(text, str):
        return None
    return _parse_prose(text)


def _parse_prose(text: str) -> dict | None:
    lowered = text.lower()
    action = next((a for a in ACTIONS if "_" in a and re.search(rf"\b{a}\b", lowered)), None)
    action = action or next((a for a, pattern in _ACTION_PHRASES if re.search(pattern, lowered)), None)
    if not action:
        return None
    sel: dict[str, Any] = {"action": action}
    if m := re.search(_UUID, lowered):
        sel["session_id"] = m.group(0)
    if m := re.search(r"\b(q_[a-z0-9_]+)\b", lowered):
        sel["quote_id"] = m.group(1)
    if m := re.search(r"(?<![a-z])(" + "|".join(_CONTRACTORS) + r")(?![a-z])", lowered):
        sel["contractor_id"] = m.group(1)
    if m := re.search(r"\b(b\d+)\b", lowered):
        sel["bathroom_id"] = m.group(1)
    if m := re.search(r"\b(urgent|full)\b", lowered):
        sel["scope"] = m.group(1)
    if m := re.search(r"\bpart\D{0,3}([12])\b", lowered):
        sel["part"] = m.group(1)
    if m := re.search(r"prepare_packet\W+(yes|no)\b", lowered):
        sel["prepare_packet"] = m.group(1)
    elif action == "accept_funding":
        sel["prepare_packet"] = "yes" if re.search(r"grant packet|application packet", lowered) else "no"
    for key in ("fix_item_ids", "hazard_ids"):
        if m := re.search(key + r"\W+([a-z0-9_]+(?:\s*,\s*[a-z0-9_]+)*)", lowered):
            sel[key] = ",".join(part.strip() for part in m.group(1).split(","))
    if action == "submit_steadi":
        sel.update(_steadi_answers_from_prose(lowered))
    if action == "submit_intake":
        sel.update(_intake_from_prose(lowered))
    return sel


def _steadi_answers_from_prose(lowered: str) -> dict[str, bool]:
    """Question ids the planner reports as checked/true/yes; unmentioned ids are left out (treated as No)."""
    answers = {}
    for m in re.finditer(r"\b(q\d{1,2}_[a-z_]+?)\b(?:\W+(?:is\s+|was\s+|=\s*)?(true|false|yes|no|checked|unchecked))?", lowered):
        value = m.group(2)
        answers[m.group(1)] = value not in ("false", "no", "unchecked")
    return answers


def _intake_from_prose(lowered: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if m := re.search(r"\bage\D{0,6}(\d{2,3})\b|\b(\d{2,3})[- ]years?[- ]old", lowered):
        out["age"] = int(m.group(1) or m.group(2))
    if m := re.search(r"\b(owner|renter|owns|rents)\b", lowered):
        out["tenure"] = "renter" if m.group(1) in ("renter", "rents") else "owner"
    if m := re.search(r"household(?:_size| size)?\D{0,6}(\d{1,2})\b", lowered):
        out["household_size"] = int(m.group(1))
    if m := re.search(r"income\D{0,8}\$?\s?(\d[\d,]*)(k)?", lowered):
        value = int(m.group(1).replace(",", ""))
        out["annual_income"] = value * 1000 if m.group(2) else value
    if m := re.search(r"\b(\d{5})\b", lowered):
        out["zip_code"] = m.group(1)
    if m := re.search(r"\b(sample_ma_plan_[a-z]|none)\b", lowered):
        out["insurance_plan"] = m.group(1)
    return out


# ---- sample payloads for Card Playground -----------------------------------

def sample_cards(session_id: str = "3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b") -> dict[str, tuple[str, dict]]:
    """One payload per screen with demo data (Maria, East Los Angeles). Keys are the out/cards file names."""
    steadi_src = load_steadi()["source_url"]
    hazards = [
        {"id": "bath_no_grab_bar_tub", "room": "bathroom", "label": "No grab bar inside the tub"},
        {"id": "bath_no_grab_bar_toilet", "room": "bathroom", "label": "No grab bar next to the toilet"},
        {"id": "path_dark_bed_to_bath", "room": "bed_to_bath", "label": "Dark path from bed to bathroom"},
    ]
    fixes = [{"id": "grab_bar_tub", "label": "Grab bar inside the tub"},
             {"id": "grab_bar_toilet", "label": "Grab bar next to the toilet"},
             {"id": "non_slip_mat", "label": "Non-slip mat in the tub"},
             {"id": "night_light", "label": "Night light on the bed-to-bath path"}]
    quotes = [
        {"quote_id": "q_juniper_1", "contractor_id": "juniper", "contractor_name": "Juniper Home Repair", "total_cents": 63_900,
         "items": fixes, "earliest_date": "2026-10-22", "badges": ["Lowest price"], "expires_at": "2026-10-24"},
        {"quote_id": "q_mesa_1", "contractor_id": "mesa", "contractor_name": "Mesa Handyworks", "total_cents": 74_550,
         "items": fixes, "earliest_date": "2026-10-19", "badges": ["Earliest: 2 days"], "expires_at": "2026-10-24"},
        {"quote_id": "q_arbor_1", "contractor_id": "arbor", "contractor_name": "Arbor CAPS Remodeling", "total_cents": 85_200,
         "items": fixes, "earliest_date": "2026-10-21", "badges": ["CAPS certified"], "expires_at": "2026-10-24"},
    ]
    funding = {
        "total_cents": 74_550, "covered_cents": 56_000, "family_share_cents": 18_550, "chart_url": "charts/waterfall_demo.png",
        "matches": [
            {"program_id": "sample_ma_plan_a", "name": "Sample plan A: home safety benefit (B14c)", "amount_cents": 16_000,
             "source_url": "https://www.hhs.gov/guidance/sites/default/files/hhs-guidance-documents/hpms%2520memo%2520primarily%2520health%2520related%25204-27-18_90.pdf",
             "as_of": "2026-10-17", "next_step": "Sample plan. Call the plan to confirm the benefit.", "funded_later": False},
            {"program_id": "nhs_la_minor_repair", "name": "NHS LA County Senior Minor Home Repair Grant", "amount_cents": 40_000,
             "source_url": "https://www.nhslacounty.org/affordable-lending-and-grant-services/senior-home-repair-grants-program-los-angeles/",
             "as_of": "2026-10-17", "next_step": "Apply online. Includes an occupational therapist home visit.", "funded_later": True},
        ],
    }
    labels = [f["label"] for f in fixes]
    return {
        "01_intake": intake_card(session_id),
        "02a_steadi_part1": steadi_card(session_id, 1),
        "02b_steadi_part2": steadi_card(session_id, 2),
        "03_risk_result": risk_result_card(session_id, 6, True, hazards, steadi_src, fell_past_year=True),
        "03b_low_risk": low_risk_card(session_id, 2, steadi_src),
        "04_pick_bathroom": bathroom_pick_card(session_id, [
            {"id": f"b{i}", "label": label, "image": f"bathrooms/b{i}.png"} for i, label in enumerate(
                ["Tub with shower", "Step-in shower", "Small bathroom", "Rug and clutter", "Dim bathroom"], 1)
        ], "Maria"),
        "05_after_detail": after_detail_card(session_id, "b1", "bathrooms/b1_after_marked.png", fixes, 74_550, 650_000),
        "06_quotes": quotes_carousel_card(session_id, quotes),
        "07_funding": funding_card(session_id, "q_mesa_1", funding),
        "08_plan_review": plan_review_card(session_id, "q_mesa_1", labels, "Mesa Handyworks", "2026-10-19", 74_550, 56_000, 18_550),
        "09_consent": consent_card(session_id, "q_mesa_1", "Maria", labels, "2026-10-19"),
        "10_split": split_card(session_id, "q_mesa_1", 5_565),
        "11_payment_status": payment_status_card(session_id, [
            {"name": "Sofia", "amount_cents": 2_783, "paid": True}, {"name": "Leo", "amount_cents": 2_782, "paid": False}]),
        "12_booking": booking_card(session_id, "bk_mesa_7f3a", "Mesa Handyworks", "2026-10-19", 5_565, 12_985),
        "13_completion": completion_card(session_id, "bk_mesa_7f3a", "bathrooms/b1_after.png", 12_985),
    }


TERMINAL_SAMPLES = {"03b_low_risk", "11_payment_status", "12_booking", "13_completion"}


def dump_samples(out_dir: Path = ROOT / "out" / "cards") -> list[Path]:
    """Writes <name>.json (card_payload, paste into the playground) and <name>.metadata.json per sample."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (kind, payload) in sample_cards().items():
        validate_payload(kind, payload)
        (out_dir / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")
        meta = card_metadata(kind, payload, terminal=name in TERMINAL_SAMPLES)
        (out_dir / f"{name}.metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
        written.append(out_dir / f"{name}.json")
    return written


if __name__ == "__main__":
    for path in dump_samples():
        kind, payload = sample_cards()[path.stem]
        size = len(json.dumps(payload, separators=(",", ":")).encode())
        print(f"{path.relative_to(ROOT)}  kind={kind}  bytes={size}  depth={json_depth(payload)}")

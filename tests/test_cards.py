import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.cards import (  # noqa: E402
    MAX_DEPTH,
    MAX_PAYLOAD_BYTES,
    card_message,
    card_metadata,
    dump_samples,
    element_depth,
    image_src,
    json_depth,
    parse_selection,
    sample_cards,
    stripe_metadata,
)

SID = "3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b"
SAMPLES = sample_cards(SID)


def cta_selections(kind: str, payload: dict) -> list[dict]:
    if kind == "carousel":
        return [i["primary_cta"]["selection"] for i in payload["items"]]
    if kind == "detail":
        return [c["selection"] for c in payload["ctas"]]
    if kind == "form":
        return [payload["submit_cta"]["selection"]]
    if kind == "review":
        return [payload["approve_cta"]["selection"], payload["reject_cta"]["selection"]]
    found = []

    def walk(node: dict) -> None:
        if node.get("type") == "button":
            found.append(node["action"]["selection"])
        for item in node.get("items", []):
            if "action" in item:
                found.append(item["action"]["selection"])
            for child in item.get("children", []):
                walk(child)
        for child in node.get("children", []):
            walk(child)

    walk(payload["root"])
    return found


def planner_prose(selection: dict) -> str:
    details = ", ".join(f"{k} {v}" for k, v in selection.items() if k != "action")
    return f"The user clicked {selection['action']} with {details}."


def test_twelve_screens_covered():
    assert len(SAMPLES) >= 12


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_payload_limits(name):
    kind, payload = SAMPLES[name]
    raw = json.dumps(payload, separators=(",", ":"))
    assert json.loads(raw) == payload
    assert len(raw.encode()) <= MAX_PAYLOAD_BYTES
    assert json_depth(payload) <= MAX_DEPTH
    if kind == "custom":
        assert set(payload) == {"root"}
        assert element_depth(payload["root"]) <= MAX_DEPTH
    meta = card_metadata(kind, payload)
    assert all(isinstance(v, str) for v in meta.values())
    assert meta["card_protocol_version"] == "1" and meta["requires_card_interaction"] == "true"


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_selections_round_trip(name):
    kind, payload = SAMPLES[name]
    for sel in cta_selections(kind, payload):
        assert sel["session_id"] == SID
        assert parse_selection(json.dumps(sel)) == sel
        parsed = parse_selection(planner_prose(sel))
        assert parsed is not None, planner_prose(sel)
        for key, value in sel.items():
            assert parsed.get(key) == value, (key, planner_prose(sel), parsed)


def test_every_interactive_card_has_a_cta():
    terminal = {"03b_low_risk", "11_payment_status", "12_booking", "13_completion"}
    for name, (kind, payload) in SAMPLES.items():
        if name not in terminal:
            assert cta_selections(kind, payload), name


def test_sources_shown_on_risk_and_funding():
    assert "cdc.gov/steadi" in json.dumps(SAMPLES["03_risk_result"][1])
    funding = json.dumps(SAMPLES["07_funding"][1])
    assert "nhslacounty.org" in funding and "as of" in funding


def test_prose_examples():
    assert parse_selection("The user picked quote q_mesa") == {"action": "pick_quote", "quote_id": "q_mesa", "contractor_id": "mesa"}
    assert parse_selection(f"User chose bathroom b3 for session {SID}") == {"action": "pick_bathroom", "bathroom_id": "b3", "session_id": SID}
    assert parse_selection("They approved the plan")["action"] == "approve"
    assert parse_selection("Maria agrees with the plan")["action"] == "consent"
    assert parse_selection("Maria says not yet")["action"] == "decline"
    assert parse_selection("hello there") is None


def test_form_values_from_prose():
    steadi = parse_selection("The user submitted the STEADI check part 1: q1_fallen yes, q3_unsteady true, q5_worried no.")
    assert steadi["action"] == "submit_steadi" and steadi["part"] == "1"
    assert steadi["q1_fallen"] is True and steadi["q3_unsteady"] is True and steadi["q5_worried"] is False
    intake = parse_selection("Intake submitted: age 82, owner, household size 1, income $38,000, ZIP 90022, plan sample_ma_plan_a")
    assert intake["action"] == "submit_intake"
    assert intake["age"] == 82 and intake["tenure"] == "owner" and intake["household_size"] == 1
    assert intake["annual_income"] == 38_000 and intake["zip_code"] == "90022" and intake["insurance_plan"] == "sample_ma_plan_a"


def test_json_without_action_falls_back_to_prose():
    assert parse_selection('{"bathroom_id": "b2"}') is None or parse_selection('{"bathroom_id": "b2"}')["action"] == "pick_bathroom"


def test_image_src_switchable():
    assert image_src("https://x.example/a.png") == "https://x.example/a.png"
    assert image_src("agent-storage://https://agentverse.ai/v1/storage/abc") == "agent-storage://https://agentverse.ai/v1/storage/abc"
    assert image_src("bathrooms/b1").endswith("/bathrooms/b1.png")


def test_card_message_and_terminal_flag():
    kind, payload = SAMPLES["12_booking"]
    msg = card_message("Booked!", kind, payload, terminal=True)
    meta = msg.content[-1].metadata
    assert meta["is_terminal"] == "true" and json.loads(meta["card_payload"]) == payload


def test_stripe_metadata_has_no_secret_key():
    meta = stripe_metadata("pk_test_x", "cs_secret_x", "cs_test_x", 2783)
    assert set(meta) == {"ui_mode", "publishable_key", "client_secret", "checkout_session_id", "currency", "amount_cents"}
    assert not any(str(v).startswith("sk_") for v in meta.values())


def test_dump_samples(tmp_path):
    paths = dump_samples(tmp_path)
    assert len(paths) == len(SAMPLES)
    for p in paths:
        json.loads(p.read_text())

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
    assert image_src("http://127.0.0.1:8080/c.png") == "http://127.0.0.1:8080/c.png"
    assert image_src("data:image/png;base64,AAAA") == "data:image/png;base64,AAAA"
    assert image_src("assets/bathrooms/b1.png") == "https://raw.githubusercontent.com/IsseiHasegawa/rooted/dev/assets/bathrooms/b1.png"
    assert image_src("assets/bathrooms/b1") == "https://raw.githubusercontent.com/IsseiHasegawa/rooted/dev/assets/bathrooms/b1.png"


def test_bathroom_picker_uses_repo_relative_assets():
    from shared.cards import bathroom_pick_card
    payload = bathroom_pick_card(SID, [{"id": "b1", "label": "Tub"}])[1]
    choice = payload["root"]["children"][0]["choices"][0]
    assert choice["image"] == "https://raw.githubusercontent.com/IsseiHasegawa/rooted/dev/assets/bathrooms/b1.png"


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


def test_wrapped_cta_selection():
    sel = cta_selections(*SAMPLES["06_quotes"])[1]
    wrapped = json.dumps({"selection": sel, "approved": True})
    assert parse_selection(wrapped) == sel
    assert parse_selection({"selection": sel, "approved": True}) == sel


def test_wrapped_form_submit_with_fields():
    fields = {"action": "submit_intake", "session_id": SID, "age": 82, "community": "East Los Angeles", "tenure": "owner",
              "months_owned": 240, "household_size": 1, "annual_income": 38000, "insurance_plan": "sample_ma_plan_a"}
    assert parse_selection(json.dumps({"selection": fields, "approved": True})) == fields
    # Field values delivered beside the selection instead of inside it are merged too.
    beside = {"selection": {"action": "submit_steadi", "session_id": SID, "part": "1"}, "approved": True, "q1_fallen": True}
    assert parse_selection(json.dumps(beside)) == {"action": "submit_steadi", "session_id": SID, "part": "1", "q1_fallen": True}


@pytest.mark.parametrize("approved", [False, "false"])
def test_wrapped_not_approved_is_no_selection(approved):
    sel = {"action": "approve", "session_id": SID, "quote_id": "q_mesa_1"}
    assert parse_selection(json.dumps({"selection": sel, "approved": approved})) is None


def test_wrapper_without_approved_flag_still_parses():
    sel = {"action": "pick_bathroom", "session_id": SID, "bathroom_id": "b2"}
    assert parse_selection(json.dumps({"selection": sel})) == sel


FUNDING = {
    "total_cents": 74_550, "covered_cents": 56_000, "pay_now_cents": 58_550, "family_share_cents": 18_550,
    "matches": [
        {"program_id": "sample_ma_plan_a", "name": "Sample plan A", "amount_cents": 16_000, "source_url": "https://x", "as_of": "2026-10-17", "funded_later": False},
        {"program_id": "nhs", "name": "NHS LA County", "amount_cents": 40_000, "source_url": "https://y", "as_of": "2026-10-17", "funded_later": True},
    ],
}


def test_funding_summary_due_now_and_deposit():
    from shared.cards import funding_summary
    m = funding_summary(FUNDING)
    assert m["due_now_cents"] == 58_550 and m["deposit_cents"] == 17_565 and m["balance_cents"] == 40_985
    assert m["grant_cents"] == 40_000 and m["grant_names"] == ["NHS LA County"]
    assert m["family_after_all_cents"] == 18_550
    # Without pay_now_cents, due now is total minus benefits paid now; grants never reduce it.
    legacy = {k: v for k, v in FUNDING.items() if k != "pay_now_cents"}
    assert funding_summary(legacy)["due_now_cents"] == 58_550


def test_money_wording_on_funding_review_split():
    from shared.cards import funding_card, plan_review_card, split_card
    texts = {
        "funding": json.dumps(funding_card(SID, "q_mesa_1", FUNDING)[1]),
        "review": json.dumps(plan_review_card(SID, "q_mesa_1", ["Grab bar"], "Mesa", "2026-10-19", FUNDING)[1]),
    }
    for name, text in texts.items():
        assert "$585.50" in text and "$175.65" in text, name
        assert "Grants may reimburse up to $400 after approval (NHS LA County)" in text, name
        assert "$185.50" in text and "not guaranteed" in text, name
        assert "guaranteed approval" not in text.lower()
    split = split_card(SID, "q_mesa_1", FUNDING)[1]
    assert split["title"] == "Split the $175.65 deposit (30% of $585.50 due now)"


def test_no_grant_line_without_grants():
    from shared.cards import funding_card
    only_insurance = {**FUNDING, "matches": FUNDING["matches"][:1], "family_share_cents": 58_550}
    assert "Grants may reimburse" not in json.dumps(funding_card(SID, "q_mesa_1", only_insurance)[1])


def test_funding_result_model_accepted():
    from shared.cards import funding_card, funding_summary
    from shared.models import FundingResult
    model = FundingResult(session_id=SID, **FUNDING)
    assert funding_summary(model)["due_now_cents"] == 58_550
    assert funding_card(SID, "q_mesa_1", model)[0] == "custom"

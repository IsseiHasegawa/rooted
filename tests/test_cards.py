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
    terminal = {"03b_low_risk", "07b_wait_picf", "11_payment_status", "12_booking", "13_completion"}
    for name, (kind, payload) in SAMPLES.items():
        if name not in terminal:
            assert cta_selections(kind, payload), name


def test_sources_shown_on_risk_and_funding():
    assert "cdc.gov/steadi" in json.dumps(SAMPLES["03_risk_result"][1])
    funding = json.dumps(SAMPLES["07_funding"][1])
    assert "lacda.org" in funding and "as of" in funding


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


RISK_RESULT_PROSE = (f"The user pressed \"Find the fixes\" on the CDC STEADI fall-risk check result for session {SID} "
                     "(hazard_ids bath_slippery_tub,path_dark_bed_to_bath).")


def test_find_the_fixes_is_not_read_as_a_steadi_submission():
    # Regression: the bare word "steadi" in planner prose used to win over "continue".
    assert parse_selection(RISK_RESULT_PROSE, {"continue"})["action"] == "continue"
    assert parse_selection("The user clicked continue on the STEADI result card")["action"] == "continue"


def test_current_card_actions_win_in_prose():
    # Planner prose repeating earlier card text: the action the current card offers is chosen.
    text = "After the fall-risk check, the user approved the plan."
    assert parse_selection(text)["action"] == "submit_steadi"
    assert parse_selection(text, {"approve", "cancel"})["action"] == "approve"
    # Nothing allowed matches: the general parse still answers, so the orchestrator can re-send the card.
    assert parse_selection("They approved the plan", {"continue"})["action"] == "approve"
    # JSON keeps its explicit action whatever the card.
    assert parse_selection('{"action": "submit_steadi"}', {"continue"})["action"] == "submit_steadi"


def test_orchestrator_advances_from_risk_result_on_planner_prose():
    import asyncio
    from types import SimpleNamespace
    from unittest import mock

    from agents.orchestrator import flow
    from shared.runtime import text_message

    store = {}
    ctx = SimpleNamespace(storage=SimpleNamespace(get=store.get, set=store.__setitem__),
                          logger=mock.Mock(), send=mock.AsyncMock(), session="unused")
    s = {"session_id": SID, "sender": "agent1user", "state": "RISK_RESULT", "card": None,
         "data": {"intake": {"parent_name": "Maria"}, "risk": {"hazards": []}}}
    flow.save(ctx, s)
    store[flow._active_key("agent1user")] = SID
    asyncio.run(flow.handle_chat(ctx, "agent1user", text_message(RISK_RESULT_PROSE)))
    assert flow.load(ctx, SID)["state"] == "PICK_BATHROOM"


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
    image, button = payload["root"]["children"][0]["children"][1:]
    assert image["src"] == "https://raw.githubusercontent.com/IsseiHasegawa/rooted/dev/assets/bathrooms/b1.png"
    assert button["action"]["selection"] == {"action": "pick_bathroom", "session_id": SID, "bathroom_id": "b1"}


def test_bathroom_picker_shows_full_width_images_in_a_wide_drawer():
    from shared.cards import WIDE_DRAWER_PX, bathroom_pick_card
    rooms = [{"id": f"b{i}", "label": f"Room {i}"} for i in range(1, 6)]
    kind, payload = bathroom_pick_card(SID, rooms)
    images = [g["children"][1] for g in payload["root"]["children"]]
    assert [i["aspect_ratio"] for i in images] == ["16:9"] * 5
    assert element_depth(payload["root"]) <= MAX_DEPTH
    meta = card_metadata(kind, payload, drawer_width_px=WIDE_DRAWER_PX)
    assert meta["preferred_drawer_width_px"] == "800"
    assert "preferred_drawer_width_px" not in card_metadata(kind, payload)


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
    "total_cents": 374_550, "covered_cents": 316_000, "pay_now_cents": 58_550, "family_share_cents": 58_550,
    "pay_now_item_ids": ["grab_bar_tub", "night_light"], "funded_later_item_ids": ["tub_to_shower"],
    "matches": [
        {"program_id": "sample_ma_plan_a", "name": "Sample plan A", "amount_cents": 16_000, "covered_item_ids": ["grab_bar_tub"],
         "source_url": "https://x", "as_of": "2026-10-17", "funded_later": False},
        {"program_id": "lacda_senior_grant", "name": "LACDA Senior Grant", "amount_cents": 300_000, "covered_item_ids": ["tub_to_shower"],
         "source_url": "https://y", "as_of": "2026-10-17", "funded_later": True},
    ],
}
LABELS = {"grab_bar_tub": "Grab bar inside the tub", "night_light": "Night light", "tub_to_shower": "Tub to walk-in shower"}
TODAY = "Today: urgent safety fixes (Grab bar inside the tub, Night light), $585.50 due now, 30% deposit $175.65"
LATER = "LACDA Senior Grant covers the $3,000 tub to walk-in shower (program max $20,000, approval not guaranteed)"


def test_funding_summary_two_tracks():
    from shared.cards import funding_summary
    m = funding_summary(FUNDING, 30, LABELS)
    assert m["due_now_cents"] == 58_550 and m["deposit_cents"] == 17_565 and m["balance_cents"] == 40_985
    assert m["today_items"] == ["Grab bar inside the tub", "Night light"]
    assert m["grants"] == [{"name": "LACDA Senior Grant", "amount_cents": 300_000, "max_cents": 2_000_000, "item_ids": ["tub_to_shower"],
                            "items": ["Tub to walk-in shower"]}]
    # pay_now_cents is the source of truth; grants never reduce or join today's payment.
    assert funding_summary({**FUNDING, "pay_now_cents": 40_000})["deposit_cents"] == 12_000
    legacy = {k: v for k, v in FUNDING.items() if k != "pay_now_cents"}
    assert funding_summary(legacy)["due_now_cents"] == 374_550 - 16_000


def review_rows(payload: dict) -> dict[str, str]:
    rows = next(c for c in payload["root"]["children"] if c["type"] == "list")["items"]
    return {r["children"][0]["value"]: r["children"][1]["value"] for r in rows}


def test_two_track_wording_on_funding_review_split():
    from shared.cards import funding_card, plan_review_card, split_card
    funding = json.dumps(funding_card(SID, "q_mesa_1", FUNDING, 30, LABELS)[1])
    assert TODAY in funding and "After grant approval: " + LATER in funding
    review = plan_review_card(SID, "q_mesa_1", ["Grab bar"], "Mesa", "2026-10-19", FUNDING, 30, LABELS)[1]
    rows = review_rows(review)
    assert rows["Today"] == TODAY and rows["After grant approval"] == LATER
    for text in (funding, json.dumps(review)):
        assert "guaranteed approval" not in text.lower() and "will be approved" not in text.lower()
    split = split_card(SID, "q_mesa_1", FUNDING)[1]
    assert split["title"].startswith("Split the $175.65 deposit: 30% of $585.50 due now for urgent safety fixes")


def test_labels_fall_back_to_readable_ids():
    from shared.cards import funding_summary
    m = funding_summary(FUNDING)
    assert m["today_items"] == ["Grab bar tub", "Night light"] and m["grants"][0]["items"] == ["Tub to shower"]


def test_no_grant_line_without_grants():
    from shared.cards import funding_card
    only_insurance = {**FUNDING, "matches": FUNDING["matches"][:1], "funded_later_item_ids": []}
    assert "After grant approval" not in json.dumps(funding_card(SID, "q_mesa_1", only_insurance)[1])


def test_funding_result_model_accepted():
    from shared.cards import funding_card, funding_summary
    from shared.models import FundingResult
    model = FundingResult(session_id=SID, **FUNDING)
    assert funding_summary(model)["due_now_cents"] == 58_550
    assert funding_card(SID, "q_mesa_1", model)[0] == "custom"


def test_grant_line_without_known_max():
    from shared.cards import funding_summary, grant_lines
    unknown = {**FUNDING, "matches": [{**FUNDING["matches"][1], "program_id": "picf_hud_home_modification", "name": "PICF"}]}
    assert grant_lines(funding_summary(unknown, 30, LABELS)) == [
        "After grant approval: PICF covers the $3,000 tub to walk-in shower (approval not guaranteed)"]


PICF = {"program_id": "picf_hud_home_modification", "name": "PICF", "amount_cents": 58_550,
        "covered_item_ids": ["grab_bar_tub", "night_light"], "source_url": "https://www.picf.org/individuals-caregivers/",
        "as_of": "2026-10-17", "funded_later": True, "alternative_to_pay_now": True}


def test_picf_alternative_track():
    from shared.cards import funding_card, funding_summary
    funding = {**FUNDING, "matches": FUNDING["matches"] + [PICF]}
    m = funding_summary(funding, 30, LABELS)
    assert m["due_now_cents"] == 58_550 and [g["name"] for g in m["grants"]] == ["LACDA Senior Grant"]
    assert m["alternatives"][0]["items"] == ["Grab bar inside the tub", "Night light"]
    payload = funding_card(SID, "q_mesa_1", funding, 30, LABELS)[1]
    text = json.dumps(payload)
    picf = next(i["children"] for i in payload["root"]["children"] if i.get("type") == "list" for i in i["items"]
                if i["children"][0]["value"] == "PICF")
    # Name, amount badge, status badge, one-line source: nothing else per program.
    assert [c["type"] for c in picf] == ["text", "badge", "badge", "text"]
    assert picf[1]["label"] == "Could cover $585.50 of these items"
    assert picf[2]["label"] == "Only if you wait for approval"
    assert picf[3]["value"] == "Source: picf.org, as of 2026-10-17"
    assert "no maximum" not in text.lower()
    assert "reimburse" not in text.lower() and "pays back" not in text.lower()
    choices = {(s.get("choice"), s.get("prepare_packet")) for s in cta_selections("custom", payload)}
    assert choices == {("fix_now", "no"), ("fix_now", "yes"), ("wait_picf", None)}
    wait = next(s for s in cta_selections("custom", payload) if s["choice"] == "wait_picf")
    assert wait["program_id"] == "picf_hud_home_modification"
    parsed = parse_selection(planner_prose(wait))
    assert {k: parsed.get(k) for k in wait} == wait


def test_no_wait_button_without_alternative():
    from shared.cards import funding_card
    payload = funding_card(SID, "q_mesa_1", FUNDING, 30, LABELS)[1]
    assert {s.get("choice") for s in cta_selections("custom", payload)} == {"fix_now"}


def test_accept_funding_prose_defaults_to_fix_now():
    assert parse_selection("The user chose to accept_funding")["choice"] == "fix_now"
    assert parse_selection("They want to wait for PICF")["choice"] == "wait_picf"


def test_wait_card_is_from_program_record():
    from shared.cards import _program, wait_for_program_card
    kind, payload = wait_for_program_card(SID, _program("picf_hud_home_modification"), ["Grab bar"])
    text = json.dumps(payload)
    assert kind == "custom" and "Nothing has been charged" in text and "Request Form" in text
    assert "62 or older" in text and "picf.org" in text and "not guaranteed" in text


def test_pending_share_wording_promises_nothing_unbuilt():
    from shared.cards import booking_card, payment_status_card
    line = "Leo's share is pending; Rooted will collect it separately."
    status = json.dumps(payment_status_card(SID, [{"name": "You", "amount_cents": 100, "paid": False},
                                                  {"name": "Leo", "amount_cents": 100, "paid": False}])[1])
    booking = json.dumps(booking_card(SID, "bk", "Mesa", "2026-10-19", 100, 200, [{"name": "Leo", "amount_cents": 100}])[1])
    for text in (status, booking):
        assert line in text and "message Rooted" not in text
    assert '"Paid"' not in status


def test_demo_defaults_make_intake_optional_and_list_the_default_first():
    from shared import demo
    from shared.cards import intake_card, split_card

    _, form = intake_card(SID, defaults=demo.MARIA_INTAKE)
    fields = {f["name"]: f for f in form["fields"]}
    assert not any(f.get("required") for f in form["fields"])
    assert fields["insurance_plan"]["options"][0]["value"] == "sample_ma_plan_a"
    assert fields["zip_code"]["placeholder"] == "90022"
    _, split = split_card(SID, "q_arbor_1", FUNDING, defaults=demo.DEMO_SPLIT)
    assert split["fields"][1]["options"][0]["value"] == "even"
    # Without defaults the cards are unchanged.
    assert all(f.get("required") for f in intake_card(SID)[1]["fields"] if f["name"] in ("age", "zip_code"))


def test_demo_with_defaults_fills_only_blanks(monkeypatch):
    from shared import demo

    monkeypatch.setattr(demo, "DEMO_MODE", True)
    sel = demo.with_defaults({"action": "submit_intake", "parent_name": "Rosa", "age": ""}, demo.MARIA_INTAKE)
    assert sel["parent_name"] == "Rosa" and sel["age"] == "82" and sel["insurance_plan"] == "sample_ma_plan_a"
    monkeypatch.setattr(demo, "DEMO_MODE", False)
    assert demo.with_defaults({"age": ""}, demo.MARIA_INTAKE) == {"age": ""}


def test_fix_list_follows_the_markers_on_b1():
    # Regression: the card listed the hazard fixes (mat, night light, tub bar) while the b1 image numbers
    # tub bar, toilet bar, mat, night light.
    from agents.orchestrator.flow import marker_order
    from agents.visualizer import catalog

    hazards = ["non_slip_mat", "night_light", "grab_bar_tub"]
    assert marker_order(catalog.fallback_set("b1"), hazards) == ["grab_bar_tub", "grab_bar_toilet", "non_slip_mat", "night_light"]
    # A step-in shower: its shower grab bar stands in for the tub bar; an unpictured fix of a new kind stays, last.
    assert marker_order(catalog.fallback_set("b2"), hazards + ["handheld_shower"]) == [
        "grab_bar_shower", "non_slip_mat", "night_light", "handheld_shower"]
    # No picture: the hazard order stands.
    assert marker_order([], hazards) == hazards


def test_static_render_reports_marker_order(monkeypatch):
    import asyncio

    from agents.visualizer import catalog, render

    monkeypatch.setenv("ROOTED_IMAGE_BASE_URL", "https://example.test/")
    out = asyncio.run(render.render("b1", ["non_slip_mat", "night_light", "grab_bar_tub"]))
    assert out["fix_item_ids"] == catalog.fallback_set("b1")


def test_every_pictured_fix_has_a_price():
    # An unpriced fix would drop out of the list and shift every "Fix n" off its marker.
    from agents.orchestrator.flow import PRICES
    from agents.visualizer import catalog

    for bathroom_id in catalog.BATHROOM_IDS:
        assert set(catalog.fallback_set(bathroom_id)) <= set(PRICES), bathroom_id


def test_failover_narration():
    from agents.orchestrator.flow import quotes_narration
    assert quotes_narration(["Mesa Handyworks"], 2) == "Mesa Handyworks didn't respond within 15 s, so I compared the other two."
    assert quotes_narration([], 3) == "3 contractors answered."


def test_quote_buttons_are_primary():
    kind, payload = SAMPLES["06_quotes"]
    buttons = [c for g in payload["root"]["children"] for c in g["children"] if c["type"] == "button"]
    assert kind == "custom" and buttons and all(b["primary"] and b["label"] == "Pick this quote" for b in buttons)


def test_steadi_cards_say_how_to_answer():
    from shared.cards import steadi_card
    assert all("Tap each statement that applies." in steadi_card(SID, part)[1]["title"] for part in (1, 2))


def test_review_and_consent_have_primary_approve_buttons():
    from shared.cards import consent_card, plan_review_card
    for kind, payload in (plan_review_card(SID, "q_arbor_1", ["Grab bar"], "Arbor", "2026-10-22", FUNDING, 30, LABELS),
                          consent_card(SID, "q_arbor_1", "Maria", ["Grab bar"], "2026-10-22")):
        approve, reject = next(c for c in payload["root"]["children"] if c["type"] == "group")["children"]
        assert kind == "custom" and approve["primary"] is True and not reject.get("primary")
        assert approve["label"] in ("Approve plan", "Maria agrees")
        assert element_depth(payload["root"]) <= MAX_DEPTH


def test_review_insurance_line_is_not_a_second_deduction():
    from shared.cards import plan_review_card
    rows = review_rows(plan_review_card(SID, "q_arbor_1", ["Grab bar"], "Arbor", "2026-10-22", FUNDING, 30, LABELS)[1])
    label = "Insurance benefit (already included above)"
    assert label in rows and not rows[label].startswith("-")


def test_completion_card_names_this_chats_half():
    from shared.cards import completion_card
    text = json.dumps(completion_card(SID, "bk_arbor_1", "assets/fallback/b1/after.png", 44_341, 22_171, ["Leo"], "half"))
    assert "The remaining $443.41 is now due. Your half: $221.71; Leo's half is pending." in text
    alone = json.dumps(completion_card(SID, "bk_arbor_1", "assets/fallback/b1/after.png", 44_341))
    assert "The remaining $443.41 is now due." in alone and "Your" not in alone


def test_income_field_is_text_shown_in_dollars():
    from shared import demo
    from shared.cards import intake_card
    field = next(f for f in intake_card(SID, defaults=demo.MARIA_INTAKE)[1]["fields"] if f["name"] == "annual_income")
    assert field["kind"] == "text" and field["placeholder"] == "$38,000"
    from agents.orchestrator.flow import _int
    assert _int("$38,000") == 38_000

import json
import sys
from pathlib import Path
from unittest import mock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.risk import logic  # noqa: E402
from agents.risk.logic import HAZARDS, STEADI, map_concerns_keywords, map_concerns_llm, score_steadi, top_hazards  # noqa: E402

ONE_POINT = [q["id"] for q in STEADI["questions"] if q["points"] == 1]


def yes(*ids):
    return {i: True for i in ids}


def test_steadi_data_matches_cdc_brochure():
    assert len(STEADI["questions"]) == 12
    assert {q["id"]: q["points"] for q in STEADI["questions"] if q["points"] == 2} == {"q1_fallen": 2, "q2_cane_walker": 2}
    assert sum(q["points"] for q in STEADI["questions"]) == 14
    assert STEADI["source_url"].startswith("https://www.cdc.gov/")


def test_three_points_with_fall_is_at_risk():
    # The fall question alone is 2 points, so 3 points with a fall = fall + one 1-point answer.
    score, at_risk, fell = score_steadi(yes("q1_fallen", ONE_POINT[0]))
    assert (score, at_risk, fell) == (3, True, True)


def test_four_points_is_at_risk():
    score, at_risk, fell = score_steadi(yes(*ONE_POINT[:4]))
    assert (score, at_risk, fell) == (4, True, False)


def test_three_points_without_fall_is_not_at_risk():
    score, at_risk, fell = score_steadi(yes(*ONE_POINT[:3]))
    assert (score, at_risk, fell) == (3, False, False)


def test_fall_alone_is_at_risk():
    assert score_steadi(yes("q1_fallen")) == (2, True, True)


def test_cane_and_one_more_reaches_threshold_without_fall():
    assert score_steadi(yes("q2_cane_walker", ONE_POINT[0], ONE_POINT[1])) == (4, True, False)


def test_empty_and_all_answers():
    assert score_steadi({}) == (0, False, False)
    assert score_steadi(yes(*(q["id"] for q in STEADI["questions"]))) == (14, True, True)


def test_false_unknown_and_string_answers():
    assert score_steadi({"q1_fallen": False, "q3_unsteady": "no", "made_up": True}) == (0, False, False)
    assert score_steadi({"q1_fallen": "true", "q3_unsteady": "yes"}) == (3, True, True)


def test_legacy_fall_alias():
    assert score_steadi({"fell_past_year": True}) == (2, True, True)


def test_hazards_have_required_fields():
    for h in HAZARDS["hazards"]:
        assert {"id", "room", "label", "fix_item_ids", "source_url", "as_of"} <= set(h)
        assert h["source_url"].startswith("https://") and h["as_of"]
    assert HAZARDS["source_url"].startswith("https://")
    assert len({h["id"] for h in HAZARDS["hazards"]}) == len(HAZARDS["hazards"])


def test_default_top_hazards_cover_bathroom_and_path():
    picked = top_hazards([])
    assert len(picked) == 3
    rooms = [h["room"] for h in picked]
    assert "bathroom" in rooms and "bed_to_bath" in rooms


def test_concerns_ranked_first_and_other_rooms_ignored():
    picked = top_hazards(["bath_no_grab_bar_toilet", "stairs_handrails", "not_a_hazard"])
    assert picked[0]["id"] == "bath_no_grab_bar_toilet"
    assert all(h["id"] != "stairs_handrails" for h in picked)


def test_keyword_mapping():
    ids = map_concerns_keywords("She slips in the tub and the hallway is dark at night")
    assert "bath_slippery_tub" in ids and "path_dark_bed_to_bath" in ids
    assert map_concerns_keywords("") == []


def _fake_response(content: str, status_code: int = 200):
    resp = mock.Mock(status_code=status_code)
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def test_llm_mapping_drops_unknown_ids():
    content = json.dumps({"hazard_ids": ["bath_slippery_tub", "invented_id", "bath_slippery_tub"]})
    with mock.patch.object(logic.requests, "post", return_value=_fake_response(content)) as post:
        assert map_concerns_llm("slippery tub", api_key="test") == ["bath_slippery_tub"]
    body = post.call_args.kwargs["json"]
    assert body["model"] == "asi1-mini"
    assert body["response_format"]["json_schema"]["strict"] is True


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(logic.time, "sleep", lambda s: None)


@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError()])
def test_llm_mapping_falls_back_on_network_error(failure, no_sleep):
    with mock.patch.object(logic.requests, "post", side_effect=failure) as post:
        assert map_concerns_llm("slippery tub", api_key="test") is None
    assert post.call_count == logic.LLM_RETRIES + 1


def test_llm_mapping_retries_after_server_error(no_sleep):
    ok = _fake_response(json.dumps({"hazard_ids": ["bath_slippery_tub"]}))
    with mock.patch.object(logic.requests, "post", side_effect=[_fake_response("", 503), requests.Timeout(), ok]) as post:
        assert map_concerns_llm("slippery tub", api_key="test") == ["bath_slippery_tub"]
    assert post.call_count == 3


def test_llm_retries_fit_the_orchestrator_timeout():
    worst = (logic.LLM_RETRIES + 1) * logic.LLM_TIMEOUT_S + sum(logic.LLM_BACKOFF_S * 2 ** i for i in range(logic.LLM_RETRIES))
    assert worst < 15


def test_llm_mapping_falls_back_on_bad_json():
    with mock.patch.object(logic.requests, "post", return_value=_fake_response("not json")):
        assert map_concerns_llm("slippery tub", api_key="test") is None


def test_llm_mapping_skipped_without_key(monkeypatch):
    monkeypatch.delenv("ASI_ONE_API_KEY", raising=False)
    assert map_concerns_llm("slippery tub") is None

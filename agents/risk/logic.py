"""Deterministic STEADI scoring and hazard selection for rooted-risk. No network calls except map_concerns_llm."""
import json
import os
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
STEADI = json.loads((ROOT / "data" / "steadi.json").read_text())
HAZARDS = json.loads((ROOT / "data" / "hazards.json").read_text())
HAZARD_BY_ID = {h["id"]: h for h in HAZARDS["hazards"]}

ASI_ONE_URL = "https://api.asi1.ai/v1/chat/completions"
ASI_ONE_MODEL = "asi1-mini"
LLM_TIMEOUT_S = 10

# Older callers (and the planner) may use descriptive keys instead of steadi.json ids.
ANSWER_ALIASES = {"fell_past_year": "q1_fallen", "fallen": "q1_fallen"}


def score_steadi(answers: dict[str, bool]) -> tuple[int, bool, bool]:
    """Returns (score, at_risk, fell_past_year). Unknown ids are ignored; missing answers count as No."""
    yes = {ANSWER_ALIASES.get(k, k) for k, v in answers.items() if _truthy(v)}
    score = sum(q["points"] for q in STEADI["questions"] if q["id"] in yes)
    rule = STEADI["at_risk_rule"]
    fell = rule["fall_question_id"] in yes
    return score, score >= rule["threshold"] or fell, fell


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "on", "1", "checked")
    return bool(value)


def map_concerns_keywords(concerns: str) -> list[str]:
    text = (concerns or "").lower()
    if not text:
        return []
    hits = []
    for h in HAZARDS["hazards"]:
        if any(re.search(rf"\b{re.escape(k)}", text) for k in h["keywords"]):
            hits.append(h["id"])
    return hits


def map_concerns_llm(concerns: str, api_key: str | None = None) -> list[str] | None:
    """Asks ASI:One which checklist hazards the concern describes. Returns None on any failure."""
    api_key = api_key or os.getenv("ASI_ONE_API_KEY")
    if not api_key or not (concerns or "").strip():
        return None
    ids = list(HAZARD_BY_ID)
    catalog = "\n".join(f"- {h['id']}: {h['label']} ({h['room']})" for h in HAZARDS["hazards"])
    body = {
        "model": ASI_ONE_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "You map a family's description of an older adult's home to hazard ids from a fixed "
                                          "CDC checklist. Only use ids from the list. Return an empty list if nothing matches.\n" + catalog},
            {"role": "user", "content": concerns[:1000]},
        ],
        "response_format": {"type": "json_schema", "json_schema": {"name": "hazard_ids", "strict": True, "schema": {
            "type": "object", "additionalProperties": False, "required": ["hazard_ids"],
            "properties": {"hazard_ids": {"type": "array", "items": {"type": "string", "enum": ids}}},
        }}},
    }
    try:
        resp = requests.post(ASI_ONE_URL, json=body, headers={"Authorization": f"Bearer {api_key}"}, timeout=LLM_TIMEOUT_S)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        found = json.loads(content).get("hazard_ids", [])
    except (requests.RequestException, KeyError, IndexError, ValueError, TypeError, AttributeError):
        return None
    if not isinstance(found, list):
        return None
    return [i for i in dict.fromkeys(found) if i in HAZARD_BY_ID]


def top_hazards(matched_ids: list[str], limit: int = 3) -> list[dict]:
    """Concern-matched hazards in the focus rooms first, then the checklist defaults by priority."""
    focus = HAZARDS["focus_rooms"]
    matched = [HAZARD_BY_ID[i] for i in matched_ids if i in HAZARD_BY_ID and HAZARD_BY_ID[i]["room"] in focus]
    defaults = sorted((h for h in HAZARDS["hazards"] if h["room"] in focus), key=lambda h: (focus.index(h["room"]), h["priority"]))
    picked: list[dict] = []
    for h in matched + defaults:
        if h not in picked:
            picked.append(h)
    # Keep the bed-to-bath path in the plan when it was not explicitly ruled in or out by a concern.
    path = next((h for h in defaults if h["room"] == "bed_to_bath"), None)
    result = picked[:limit]
    if path and all(h["room"] != "bed_to_bath" for h in result) and not matched:
        result[-1] = path
    return result


def hazard_fields(h: dict) -> dict:
    return {"id": h["id"], "room": h["room"], "label": h["label"], "fix_item_ids": list(h["fix_item_ids"]),
            "source_url": h.get("source_url", HAZARDS["source_url"])}

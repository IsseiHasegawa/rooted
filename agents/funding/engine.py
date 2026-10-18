"""Deterministic funding eligibility and allocation over data/programs.json. No LLM, no network, no logging of income."""
import json
from pathlib import Path

from shared.models import FundingRequest, FundingResult, ProgramMatch

DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "programs.json"
PRICES_PATH = DATA_PATH.with_name("prices.json")


def load_scopes(path: Path = PRICES_PATH) -> dict[str, str]:
    """Item id -> "urgent" or "full" from data/prices.json. Unknown ids count as urgent."""
    try:
        with open(path) as f:
            return {i["id"]: i.get("scope", "urgent") for i in json.load(f)["items"]}
    except (OSError, KeyError, ValueError):
        return {}


def load_data(path: Path = DATA_PATH) -> dict:
    with open(path) as f:
        return json.load(f)


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("-", " ").split())


def _income_limit_usd(data: dict, household_size: int) -> int:
    table = data["lacda_income_limits_80_ami"]["by_household_size_usd"]
    return table[str(min(max(household_size, 1), 8))]


def _in_eligible_district(program: dict, community: str) -> bool:
    wanted = _norm(community)
    for district in program["eligible_districts"]:
        if wanted in {_norm(c) for c in program["unincorporated_communities"][str(district)]}:
            return True
    return False


def is_eligible(program: dict, req: FundingRequest, data: dict) -> tuple[bool, str]:
    """Return (eligible, reason). Reasons never include income values."""
    if program["kind"] == "insurance":
        if req.insurance_plan != program["id"]:
            return False, "not enrolled in this plan"
    if req.age < program["min_age"]:
        return False, f"age below {program['min_age']}"
    if req.tenure not in program["tenure"]:
        return False, f"requires tenure: {'/'.join(program['tenure'])}"
    if req.months_owned < program["min_months_owned"]:
        return False, f"owned fewer than {program['min_months_owned']} months"
    if program["area_rule"] == "lacda_unincorporated_districts" and not _in_eligible_district(program, req.community):
        return False, "community not in an eligible unincorporated district"
    if program["income_rule"] in ("lacda_80_ami", "proxy_lacda_80_ami"):
        if req.annual_income > _income_limit_usd(data, req.household_size):
            return False, "household income above the limit"
    return True, "eligible"


def _item_cost(item) -> int:
    return item.unit_price_cents * item.quantity


def allocate(req: FundingRequest, data: dict | None = None) -> FundingResult:
    """Two tracks. Due now is the urgent items minus the active insurance benefit (the real charge).
    Grants that need an application or approval never reduce it: LACDA/NHS cover the full remodel later,
    and PICF is reported as an alternative for urgent items. Grants do not reimburse work already done."""
    data = data or load_data()
    scopes = load_scopes()
    is_urgent = {i.id: scopes.get(i.id, "urgent") == "urgent" for i in req.line_items}
    cost = {i.id: _item_cost(i) for i in req.line_items}
    total = sum(cost.values())
    remaining = dict(cost)

    def take_from(prog: dict, items, left: dict[str, int]) -> tuple[int, list[str]]:
        cap = prog["max_cents"] if prog["max_cents"] is not None else sum(left.values())
        amount, ids = 0, []
        for item in items:
            if cap <= 0:
                break
            if left[item.id] <= 0 or item.category not in prog["covered_categories"]:
                continue
            if item.structural and not prog["structural_ok"]:
                continue
            got = min(cap, left[item.id])
            left[item.id] -= got
            cap -= got
            amount += got
            ids.append(item.id)
        return amount, ids

    def match(prog: dict, amount: int, ids: list[str], alternative: bool) -> ProgramMatch:
        return ProgramMatch(
            program_id=prog["id"], name=prog["name"], amount_cents=amount, covered_item_ids=ids,
            source_url=prog["source_url"], as_of=prog["as_of"], next_step=prog["next_step"],
            funded_later=prog["funded_later"], alternative_to_pay_now=alternative,
        )

    urgent_items = [i for i in req.line_items if is_urgent[i.id]]
    full_items = [i for i in req.line_items if not is_urgent[i.id]]
    matches: list[ProgramMatch] = []
    grant_item_ids: set[str] = set()
    immediate = 0  # insurance coverage on urgent items
    covered = 0

    eligible = [p for p in data["programs"] if is_eligible(p, req, data)[0]]

    # 1. Active insurance benefit: reduces what is due now.
    for prog in (p for p in eligible if p["kind"] == "insurance"):
        amount, ids = take_from(prog, urgent_items, remaining)
        if amount:
            matches.append(match(prog, amount, ids, False))
            immediate += amount
            covered += amount

    # 2. Grants limited to the full remodel: funded after approval.
    for prog in (p for p in eligible if p["kind"] == "grant" and not p["alternative_to_pay_now"]):
        amount, ids = take_from(prog, full_items, remaining)
        if amount:
            matches.append(match(prog, amount, ids, False))
            grant_item_ids.update(ids)
            covered += amount

    # 3. Alternatives for urgent items: shown, but never counted in any total.
    for prog in (p for p in eligible if p["alternative_to_pay_now"]):
        left = dict(remaining)  # what insurance did not cover; does not mutate the real allocation
        amount, ids = take_from(prog, urgent_items, left)
        if amount:
            matches.append(match(prog, amount, ids, True))

    all_ids = [i.id for i in req.line_items]
    pay_now_ids = [i for i in all_ids if is_urgent[i]]
    return FundingResult(
        session_id=req.session_id,
        matches=matches,
        total_cents=total,
        covered_cents=covered,
        family_share_cents=total - covered,
        pay_now_cents=sum(cost[i] for i in pay_now_ids) - immediate,
        pay_now_item_ids=pay_now_ids,
        funded_later_item_ids=[i for i in all_ids if i in grant_item_ids],
    )

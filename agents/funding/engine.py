"""Deterministic funding eligibility and allocation over data/programs.json. No LLM, no network, no logging of income."""
import json
from pathlib import Path

from shared.models import FundingRequest, FundingResult, ProgramMatch

DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "programs.json"


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
    data = data or load_data()
    # Insurance first (non-structural only), then grants in file order.
    programs = sorted(data["programs"], key=lambda p: 0 if p["kind"] == "insurance" else 1)

    remaining = {i.id: _item_cost(i) for i in req.line_items}
    total = sum(remaining.values())
    matches: list[ProgramMatch] = []
    grant_item_ids: set[str] = set()

    for prog in programs:
        ok, _ = is_eligible(prog, req, data)
        if not ok:
            continue
        cap = prog["max_cents"] if prog["max_cents"] is not None else sum(remaining.values())
        amount = 0
        covered_ids: list[str] = []
        for item in req.line_items:
            if cap <= 0:
                break
            if remaining[item.id] <= 0 or item.category not in prog["covered_categories"]:
                continue
            if item.structural and not prog["structural_ok"]:
                continue
            take = min(cap, remaining[item.id])
            remaining[item.id] -= take
            cap -= take
            amount += take
            covered_ids.append(item.id)
        if amount == 0:
            continue
        if prog["funded_later"]:
            grant_item_ids.update(covered_ids)
        matches.append(
            ProgramMatch(
                program_id=prog["id"],
                name=prog["name"],
                amount_cents=amount,
                covered_item_ids=covered_ids,
                source_url=prog["source_url"],
                as_of=prog["as_of"],
                next_step=prog["next_step"],
                funded_later=prog["funded_later"],
            )
        )

    covered = sum(m.amount_cents for m in matches)
    all_ids = [i.id for i in req.line_items]
    return FundingResult(
        session_id=req.session_id,
        matches=matches,
        total_cents=total,
        covered_cents=covered,
        family_share_cents=total - covered,
        pay_now_item_ids=[i for i in all_ids if i not in grant_item_ids],
        funded_later_item_ids=[i for i in all_ids if i in grant_item_ids],
    )

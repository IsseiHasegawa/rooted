from agents.funding.engine import allocate, load_data
from shared.models import FundingRequest, LineItem

def arbor(cents: int) -> int:
    return round(cents * 1.2 / 100) * 100  # whole dollars, like the contractor agent


# Ids, categories and scopes match data/prices.json: grab bars are urgent, the rest is the full remodel.
URGENT = [
    LineItem(id="grab_bar_tub", label="Grab bar (tub)", category="grab_bar", unit_price_cents=30_000),
    LineItem(id="grab_bar_toilet", label="Grab bar (toilet)", category="grab_bar", unit_price_cents=30_000),
]
FULL_ONLY = [
    LineItem(id="non_slip_flooring", label="Non-slip flooring", category="non_slip_flooring", unit_price_cents=74_400),
    LineItem(id="tub_to_shower", label="Tub to shower", category="tub_to_shower", unit_price_cents=300_000, structural=True),
]
ITEMS = URGENT + FULL_ONLY


def _price_items():
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).resolve().parent.parent / "data" / "prices.json").read_text())["items"]
    return [
        {"item": LineItem(id=i["id"], label=i["label"], category=i["category"],
                          unit_price_cents=i["unit_price_cents"], structural=i["structural"])}
        for i in data
    ]


PRICE_ITEMS = _price_items()


def req(**kw) -> FundingRequest:
    base = dict(session_id="s1", age=82, community="East Los Angeles", tenure="owner", months_owned=240,
                household_size=1, annual_income=38_000, insurance_plan="none", line_items=ITEMS)
    base.update(kw)
    return FundingRequest(**base)


def ids(res):
    return {m.program_id: m for m in res.matches}


def test_maria_urgent_due_now_grants_never_reduce_it():
    res = allocate(req())
    m = ids(res)
    assert set(m["lacda_senior_grant"].covered_item_ids) == {"non_slip_flooring", "tub_to_shower"}
    assert res.funded_later_item_ids == ["non_slip_flooring", "tub_to_shower"]
    assert res.pay_now_item_ids == ["grab_bar_tub", "grab_bar_toilet"]
    assert res.pay_now_cents == 60_000
    picf = m["picf_hud_home_modification"]
    assert picf.alternative_to_pay_now and picf.funded_later
    assert set(picf.covered_item_ids) == {"grab_bar_tub", "grab_bar_toilet"}
    assert "reimburse" in picf.next_step  # grants do not repay work already done
    # PICF is an alternative: it is not counted in coverage or the family share.
    assert res.covered_cents == m["lacda_senior_grant"].amount_cents
    assert res.family_share_cents == 60_000


def test_pay_now_ma_plan_first_then_uncovered_urgent_items():
    items = [
        LineItem(id="grab_bar_tub", label="Grab bar", category="grab_bar", unit_price_cents=27_000),
        LineItem(id="non_slip_mat", label="Non-slip mat", category="non_slip_flooring", unit_price_cents=38_400),
    ]
    res = allocate(req(insurance_plan="sample_ma_plan_a", line_items=items))
    assert res.total_cents == 65_400
    assert ids(res)["sample_ma_plan_a"].amount_cents == 27_000
    assert "picf_hud_home_modification" not in ids(res)  # plan already covered the grab bar
    assert res.pay_now_cents == 38_400
    assert res.funded_later_item_ids == []


def test_pay_now_maria_full_scope_with_arbor_and_ma_plan_a():
    items = [LineItem(**{**i["item"].dict(), "unit_price_cents": arbor(i["item"].unit_price_cents)}) for i in PRICE_ITEMS]
    res = allocate(req(insurance_plan="sample_ma_plan_a", line_items=items))
    m = ids(res)
    assert res.total_cents == 664_600
    assert m["sample_ma_plan_a"].amount_cents == 50_000 and not m["sample_ma_plan_a"].funded_later
    assert m["lacda_senior_grant"].amount_cents == 449_300
    assert m["picf_hud_home_modification"].alternative_to_pay_now
    assert "tub_to_shower" in res.funded_later_item_ids and "tub_to_shower" not in res.pay_now_item_ids
    # Urgent items ($2,153) minus the plan's $500; no grant reduces it.
    assert res.pay_now_cents == 165_300
    assert round(res.pay_now_cents * 0.30) == 49_590
    assert res.family_share_cents == 165_300  # total - MA - LACDA


def test_pay_now_pasadena_renter():
    res = allocate(req(community="Pasadena", tenure="renter", months_owned=0))
    m = ids(res)
    assert set(m) == {"picf_hud_home_modification"}  # no LACDA or NHS for a renter
    assert m["picf_hud_home_modification"].alternative_to_pay_now
    assert res.funded_later_item_ids == []
    assert res.pay_now_cents == 60_000  # urgent grab bars; PICF never lowers it
    assert res.family_share_cents == res.total_cents  # nothing counted: PICF is an alternative


def test_money_adds_up_and_sources_present():
    res = allocate(req(insurance_plan="sample_ma_plan_a"))
    assert res.covered_cents + res.family_share_cents == res.total_cents
    for match in res.matches:
        assert match.source_url.startswith("https://")
        assert match.as_of
    ma = ids(res)["sample_ma_plan_a"]
    assert "SAMPLE" in ma.name and not ma.funded_later
    # MA takes the non-structural items first; structural stays with the grant.
    assert "tub_to_shower" not in ma.covered_item_ids


def test_pasadena_renter_not_lacda_eligible():
    res = allocate(req(community="Pasadena", tenure="renter", months_owned=0))
    m = ids(res)
    assert "lacda_senior_grant" not in m
    assert "nhs_la_county_minor_repair" not in m  # owners only
    assert "picf_hud_home_modification" in m  # renters are allowed
    assert "tub_to_shower" not in m["picf_hud_home_modification"].covered_item_ids


def test_income_over_limit_is_rejected():
    res = allocate(req(annual_income=95_000))
    assert res.matches == []
    assert res.family_share_cents == res.total_cents


def test_owned_under_12_months_is_rejected_by_lacda():
    assert "lacda_senior_grant" not in ids(allocate(req(months_owned=6)))


def test_under_62_gets_nothing():
    assert allocate(req(age=60)).matches == []


def test_all_records_have_source_and_date():
    data = load_data()
    for p in data["programs"]:
        assert p["source_url"] and p["as_of"]
        if p["kind"] == "insurance":
            assert p["sample"] is True and "SAMPLE" in p["name"]


def test_altadena_eligible_and_incorporated_pasadena_not():
    assert "lacda_senior_grant" in ids(allocate(req(community="Altadena")))
    assert "lacda_senior_grant" not in ids(allocate(req(community="Pasadena")))


def test_household_of_five_uses_published_limit():
    assert "lacda_senior_grant" in ids(allocate(req(household_size=5, annual_income=140_000)))
    assert "lacda_senior_grant" not in ids(allocate(req(household_size=5, annual_income=150_000)))

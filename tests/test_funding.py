from agents.funding.engine import allocate, load_data
from shared.models import FundingRequest, LineItem

ITEMS = [
    LineItem(id="grab_bar_tub", label="Grab bar (tub)", category="grab_bar", unit_price_cents=30_000),
    LineItem(id="grab_bar_toilet", label="Grab bar (toilet)", category="grab_bar", unit_price_cents=30_000),
    LineItem(id="non_slip", label="Non-slip flooring", category="non_slip_flooring", unit_price_cents=120_000),
    LineItem(id="tub_to_shower", label="Tub to shower", category="tub_to_shower", unit_price_cents=900_000, structural=True),
]


def req(**kw) -> FundingRequest:
    base = dict(session_id="s1", age=82, community="East Los Angeles", tenure="owner", months_owned=240,
                household_size=1, annual_income=38_000, insurance_plan="none", line_items=ITEMS)
    base.update(kw)
    return FundingRequest(**base)


def ids(res):
    return {m.program_id: m for m in res.matches}


def test_maria_fully_covered_by_lacda():
    res = allocate(req())
    m = ids(res)
    assert "lacda_senior_grant" in m
    assert m["lacda_senior_grant"].amount_cents <= 2_000_000
    assert "tub_to_shower" in m["lacda_senior_grant"].covered_item_ids
    assert "nhs_la_county_minor_repair" not in m  # LACDA already covered everything NHS could
    assert res.covered_cents == res.total_cents == 1_080_000
    assert res.family_share_cents == 0
    assert set(res.funded_later_item_ids) == {i.id for i in ITEMS}
    assert res.pay_now_item_ids == []


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
    assert "picf_hud_home_modification" in m  # renters allowed
    assert "tub_to_shower" not in m["picf_hud_home_modification"].covered_item_ids
    assert res.family_share_cents >= 900_000  # structural work is not funded


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

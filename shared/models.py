"""Message models shared by every Rooted agent.

Money is always integer cents. Every request carries session_id so replies can be matched to the
family's chat session and every log line can name it.
"""
from uagents import Model


class LineItem(Model):
    id: str  # e.g. "grab_bar_tub"; matches fix_item_ids in data/hazards.json and ids in data/prices.json
    label: str
    category: str  # program coverage type, e.g. "grab_bar", "non_slip_flooring", "tub_to_shower"
    quantity: int = 1
    unit_price_cents: int = 0
    structural: bool = False


class Hazard(Model):
    id: str
    room: str
    label: str
    fix_item_ids: list[str] = []
    source_url: str = ""


# orchestrator -> rooted-risk
class AssessRequest(Model):
    session_id: str
    answers: dict[str, bool] = {}  # STEADI question id -> yes
    concerns: str = ""


class AssessResult(Model):
    session_id: str
    score: int
    at_risk: bool
    fell_past_year: bool = False
    hazards: list[Hazard] = []
    source_url: str = ""
    error: str | None = None


# orchestrator -> rooted-visualizer
class RenderRequest(Model):
    session_id: str
    bathroom_id: str
    fix_item_ids: list[str] = []  # marker numbers follow this order


class RenderResult(Model):
    session_id: str
    bathroom_id: str
    before_url: str
    after_url: str
    marked_url: str
    cached: bool = False
    error: str | None = None


# orchestrator -> rooted-funding
class FundingRequest(Model):
    session_id: str
    age: int
    community: str  # e.g. "East Los Angeles"
    tenure: str  # "owner" or "renter"
    months_owned: int = 0
    household_size: int = 1
    annual_income: int = 0  # USD per year; never log this
    insurance_plan: str = "none"  # plan id from data/programs.json, or "none"
    line_items: list[LineItem] = []


class ProgramMatch(Model):
    program_id: str
    name: str
    amount_cents: int
    covered_item_ids: list[str] = []
    source_url: str
    as_of: str
    next_step: str = ""
    funded_later: bool = False  # True when money arrives only after a grant application


class FundingResult(Model):
    session_id: str
    matches: list[ProgramMatch] = []
    total_cents: int = 0
    covered_cents: int = 0
    family_share_cents: int = 0
    pay_now_item_ids: list[str] = []
    funded_later_item_ids: list[str] = []
    chart_url: str = ""
    error: str | None = None


# orchestrator -> contractor-*
class QuoteRequest(Model):
    session_id: str
    line_items: list[LineItem] = []
    zip_code: str = ""
    scope: str = "urgent"  # "urgent" (safety fixes only) or "full" (full bathroom remodel too)


class QuotedItem(Model):
    item_id: str
    label: str
    price_cents: int


class Quote(Model):
    session_id: str
    quote_id: str
    contractor_id: str
    contractor_name: str
    total_cents: int
    items: list[QuotedItem] = []
    earliest_date: str  # ISO date
    badges: list[str] = []
    expires_at: str  # ISO date
    source_url: str = ""


class CounterOffer(Model):
    session_id: str
    quote_id: str
    target_cents: int


class RevisedQuote(Model):
    session_id: str
    quote_id: str
    accepted: bool
    total_cents: int
    message: str = ""


class BookRequest(Model):
    session_id: str
    quote_id: str
    date: str  # ISO date
    deposit_percent: int = 30


class BookConfirm(Model):
    session_id: str
    quote_id: str
    booking_id: str
    contractor_id: str
    date: str
    confirmed: bool = True
    message: str = ""

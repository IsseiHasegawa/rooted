"""DEMO_MODE=1: Maria's profile fills any intake or split field left blank, so the scripted demo is
button presses plus the Stripe test card. Every agent still exchanges real messages; only typing is skipped.
"""
import os

DEMO_MODE = os.getenv("DEMO_MODE", "").strip() == "1"

MARIA_INTAKE = {
    "parent_name": "Maria", "age": "82", "community": "East Los Angeles", "zip_code": "90022", "tenure": "owner",
    "months_owned": "240", "household_size": "1", "annual_income": "38000", "insurance_plan": "sample_ma_plan_a",
    "concern": "Slippery tub and a dark hallway at night",
}
DEMO_SPLIT = {"payers": "2", "split": "even", "payer_contacts": "@leo"}


def _blank(v) -> bool:
    return v is None or str(v).strip() == ""


def with_defaults(sel: dict, defaults: dict) -> dict:
    """The selection with every blank field taken from defaults (DEMO_MODE only)."""
    if not DEMO_MODE:
        return sel
    return {**sel, **{k: v for k, v in defaults.items() if _blank(sel.get(k))}}

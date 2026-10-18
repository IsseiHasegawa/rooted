"""Fix-item catalog: prompt wording and marker anchors per bathroom.

Anchors are (x, y) fractions of the image. Marker numbers follow the order of fix_item_ids in the
request, so the plan's line items and the picture always agree.
"""

FIX_PHRASES = {
    "grab_bar_tub": "a stainless steel grab bar mounted horizontally on the tub wall inside the tub",
    "grab_bar_toilet": "a stainless steel grab bar mounted on the wall next to the toilet",
    "grab_bar_shower": "a stainless steel grab bar mounted on the shower wall",
    "non_slip_mat": "a light grey textured non-slip mat on the floor of the tub or shower",
    "night_light": "a small night light plugged into a wall outlet, softly glowing",
    "shower_seat": "a fold-down white shower seat on the shower wall",
    "handheld_shower": "a handheld shower head on a slide bar",
    "raised_toilet_seat": "a raised toilet seat with arms",
    "remove_rug": "the loose bath rug replaced by a bonded non-slip floor",
}

# Default anchor per fix id when a bathroom has no specific entry.
DEFAULT_ANCHORS = {
    "grab_bar_tub": (0.30, 0.42),
    "grab_bar_toilet": (0.80, 0.55),
    "grab_bar_shower": (0.30, 0.42),
    "non_slip_mat": (0.45, 0.88),
    "night_light": (0.90, 0.30),
    "shower_seat": (0.25, 0.55),
    "handheld_shower": (0.20, 0.30),
    "raised_toilet_seat": (0.80, 0.70),
    "remove_rug": (0.55, 0.92),
}

# Per-bathroom overrides, tuned by eye against the generated rooms.
ANCHORS: dict[str, dict[str, tuple[float, float]]] = {
    "b1": {"grab_bar_tub": (0.81, 0.46), "grab_bar_toilet": (0.37, 0.545), "non_slip_mat": (0.78, 0.74), "night_light": (0.10, 0.50)},
    "b2": {"grab_bar_shower": (0.58, 0.40), "non_slip_mat": (0.52, 0.90), "night_light": (0.08, 0.48)},
    "b3": {"grab_bar_toilet": (0.30, 0.45), "non_slip_mat": (0.69, 0.93), "night_light": (0.525, 0.865)},
    "b4": {"grab_bar_tub": (0.39, 0.43), "non_slip_mat": (0.25, 0.90), "night_light": (0.50, 0.36)},
    "b5": {"grab_bar_tub": (0.60, 0.47), "non_slip_mat": (0.71, 0.80), "night_light": (0.365, 0.34)},
}

# Fix set baked into each pre-rendered "after" image, in marker order. Only items that fit what the photo shows.
FALLBACK_SETS = {
    "b1": ["grab_bar_tub", "grab_bar_toilet", "non_slip_mat", "night_light"],
    "b2": ["grab_bar_shower", "non_slip_mat", "night_light"],
    "b3": ["grab_bar_toilet", "non_slip_mat", "night_light"],
    "b4": ["grab_bar_tub", "non_slip_mat", "night_light"],
    "b5": ["grab_bar_tub", "non_slip_mat", "night_light"],
}

# Prompt wording that depends on what the room looks like.
PHRASE_OVERRIDES = {
    ("b2", "grab_bar_shower"): "a vertical stainless steel grab bar on the grey tile wall just left of the shower door, at hand height",
    ("b2", "non_slip_mat"): "a light grey non-slip mat on the tile floor just outside the shower door",
    ("b2", "night_light"): "a small glowing night light plugged into the wall outlet on the left wall",
    ("b3", "grab_bar_toilet"): "a stainless steel grab bar mounted horizontally on the tile wall behind the toilet tank, above the tank lid",
    ("b3", "non_slip_mat"): "a light grey rubber non-slip mat on the floor in front of the shower, replacing the crumpled grey cloth",
    ("b3", "night_light"): "a small glowing night light plugged into a wall outlet low on the tile wall near the toilet",
    ("b4", "grab_bar_tub"): "a stainless steel grab bar mounted on the white tub wall at the right end of the tub",
    ("b4", "non_slip_mat"): "a light grey rubber non-slip mat on the floor replacing the loose blue bath rug",
    ("b4", "night_light"): "a small glowing night light plugged into a wall outlet on the blue wall between the tub and the mirror",
    ("b5", "grab_bar_tub"): "a horizontal stainless steel grab bar on the white tub wall above the soap dish",
    ("b5", "non_slip_mat"): "a light grey non-slip mat on the floor of the tub",
    ("b5", "night_light"): "a small glowing night light plugged into the wall outlet on the wall between the vanity and the tub",
}

BATHROOM_IDS = ["b1", "b2", "b3", "b4", "b5"]


def fallback_set(bathroom_id: str) -> list[str]:
    return FALLBACK_SETS.get(bathroom_id, [])


def phrase(fix_id: str, bathroom_id: str = "") -> str:
    if (bathroom_id, fix_id) in PHRASE_OVERRIDES:
        return PHRASE_OVERRIDES[(bathroom_id, fix_id)]
    return FIX_PHRASES.get(fix_id, fix_id.replace("_", " "))


def anchor(bathroom_id: str, fix_id: str, index: int) -> tuple[float, float]:
    a = ANCHORS.get(bathroom_id, {}).get(fix_id) or DEFAULT_ANCHORS.get(fix_id)
    if a:
        return a
    return (0.15 + 0.12 * (index % 6), 0.15)  # unknown fix: visible, unobtrusive row

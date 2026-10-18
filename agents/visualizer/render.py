"""Render pipeline: before/after/marked images, funding waterfall, cache and URL building.

Gemini sits behind gemini_edit(); everything else works without it (fallback image, then Pillow placeholder).
"""
import asyncio
import hashlib
import io
import logging
import os
import textwrap
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from agents.visualizer import catalog  # noqa: E402

log = logging.getLogger("visualizer")

ROOT = Path(__file__).resolve().parent.parent.parent
BATHROOMS = ROOT / "assets" / "bathrooms"  # real generated outputs only
FALLBACK = ROOT / "assets" / "fallback"  # <id>/after.png pre-rendered per catalog.FALLBACK_SETS
PLACEHOLDERS = ROOT / "assets" / "placeholders"  # Pillow stand-ins, never real outputs
CACHE = ROOT / "assets" / "cache"
CHARTS = ROOT / "assets" / "charts"

MODEL = "gemini-2.5-flash-image"
GEMINI_TIMEOUT_S = 25
FONT = "/System/Library/Fonts/Helvetica.ttc"

# The wording that held the room best in the P0-C spike. Updated once Gabriel picks a variant.
EDIT_PROMPT = (
    "Edit this photo of the bathroom. Keep the same room, walls, tiles, lighting and camera angle. Only add: {items}."
)


def _font(size: int):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def cache_key(bathroom_id: str, fix_ids: list[str]) -> str:
    # Order of fixes decides marker numbers, but the rendered "after" only depends on the set.
    raw = bathroom_id + "|" + ",".join(sorted(fix_ids))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def placeholder(bathroom_id: str, label: str) -> Path:
    PLACEHOLDERS.mkdir(parents=True, exist_ok=True)
    path = PLACEHOLDERS / f"{bathroom_id}_{label}.png"
    if not path.exists():
        im = Image.new("RGB", (1024, 683), (226, 220, 208))
        d = ImageDraw.Draw(im)
        d.text((512, 341), f"{bathroom_id} {label} (placeholder)", fill=(90, 84, 74), font=_font(36), anchor="mm")
        im.save(path)
    return path


def draw_markers(src: Path, dst: Path, bathroom_id: str, fix_ids: list[str]) -> None:
    im = Image.open(src).convert("RGB")
    d = ImageDraw.Draw(im)
    w, h = im.size
    r = max(18, w // 40)
    f = _font(int(r * 1.3))
    for i, fid in enumerate(fix_ids, 1):
        fx, fy = catalog.anchor(bathroom_id, fid, i - 1)
        x, y = fx * w, fy * h
        d.ellipse([x - r - 3, y - r - 3, x + r + 3, y + r + 3], fill="white")
        d.ellipse([x - r, y - r, x + r, y + r], fill=(214, 40, 40))
        d.text((x, y), str(i), fill="white", font=f, anchor="mm")
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst)


def _gemini_edit_sync(before: Path, fix_ids: list[str], bathroom_id: str = "") -> bytes | None:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    prompt = EDIT_PROMPT.format(items=", ".join(catalog.phrase(f, bathroom_id) for f in fix_ids))
    r = client.models.generate_content(
        model=MODEL,
        contents=[prompt, Image.open(before)],
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )
    for p in (r.candidates[0].content.parts if r.candidates and r.candidates[0].content else []):
        if p.inline_data:
            return p.inline_data.data
    return None


async def gemini_edit(before: Path, fix_ids: list[str], bathroom_id: str = "") -> bytes | None:
    """The single plug-in point for Gemini. Returns PNG/JPEG bytes or None (caller falls back)."""
    if os.getenv("VISUALIZER_GEMINI") != "1" or not os.getenv("GOOGLE_API_KEY"):
        return None
    try:
        return await asyncio.wait_for(asyncio.to_thread(_gemini_edit_sync, before, fix_ids, bathroom_id), GEMINI_TIMEOUT_S)
    except Exception as exc:  # quota, timeout, safety block: all fall back
        log.warning("gemini edit failed: %s", type(exc).__name__)
        return None


def _before_path(bathroom_id: str) -> Path:
    p = BATHROOMS / f"{bathroom_id}.png"
    return p if p.exists() else placeholder(bathroom_id, "before")


def _fallback_after(bathroom_id: str, fix_ids: list[str]) -> Path | None:
    p = FALLBACK / bathroom_id / "after.png"
    if p.exists() and set(fix_ids) <= set(catalog.fallback_set(bathroom_id)):
        return p
    return None


def _static_set(bathroom_id: str) -> dict | None:
    """The committed, publicly hosted images for a demo bathroom, or None if any is missing."""
    before = BATHROOMS / f"{bathroom_id}.png"
    after = FALLBACK / bathroom_id / "after.png"
    marked = FALLBACK / bathroom_id / "marked.png"
    if before.exists() and after.exists() and marked.exists():
        return {"before": before, "after": after, "marked": marked, "cached": True, "source": "static"}
    return None


async def render(bathroom_id: str, fix_ids: list[str], session_id: str = "-") -> dict:
    """Return local paths {before, after, marked, cached, source, fix_item_ids (marker order)}."""
    t0 = time.monotonic()
    if os.getenv("ROOTED_IMAGE_BASE_URL"):
        # Only committed files are reachable over https, so serve the nearest pre-rendered set.
        # Markers on it follow the demo fix order (catalog.fallback_set(bathroom_id)), not necessarily the request.
        static = _static_set(bathroom_id)
        log.info("[%s] render %s static=%s", session_id, bathroom_id, bool(static))
        if static is None:
            raise FileNotFoundError(bathroom_id)
        return {**static, "fix_item_ids": catalog.fallback_set(bathroom_id)}
    key = cache_key(bathroom_id, fix_ids)
    before = _before_path(bathroom_id)
    # Results built on a placeholder "before" must never poison the real cache.
    out_dir = PLACEHOLDERS if before.parent == PLACEHOLDERS else CACHE
    after = out_dir / f"{key}_after.png"
    # Marked depends on marker order, so the order is part of its name.
    order = hashlib.sha1(",".join(fix_ids).encode()).hexdigest()[:6]
    marked = out_dir / f"{key}_{order}_marked.png"
    out_dir.mkdir(parents=True, exist_ok=True)

    if after.exists() and marked.exists():
        log.info("[%s] render %s cache hit %.2fs", session_id, bathroom_id, time.monotonic() - t0)
        return {"before": before, "after": after, "marked": marked, "cached": True, "source": "cache", "fix_item_ids": fix_ids}

    source = "gemini"
    if not after.exists():
        data = await gemini_edit(before, fix_ids, bathroom_id)
        if data:
            Image.open(io.BytesIO(data)).convert("RGB").save(after)
        elif (fb := _fallback_after(bathroom_id, fix_ids)):
            source = "fallback"
            Image.open(fb).convert("RGB").save(after)
        else:
            source = "placeholder"
            Image.open(placeholder(bathroom_id, "after")).save(after)
    else:
        source = "cache"
    draw_markers(after, marked, bathroom_id, fix_ids)
    log.info("[%s] render %s source=%s %.2fs", session_id, bathroom_id, source, time.monotonic() - t0)
    return {"before": before, "after": after, "marked": marked, "cached": False, "source": source, "fix_item_ids": fix_ids}


def waterfall_path(total_cents: int, covered: list[tuple[str, int]], family_share_cents: int, out_dir: Path = CHARTS) -> Path:
    key = hashlib.sha1(repr((total_cents, covered, family_share_cents)).encode()).hexdigest()[:12]
    return out_dir / f"waterfall_{key}.png"


def render_waterfall(total_cents: int, covered: list[tuple[str, int]], family_share_cents: int, out_dir: Path = CHARTS) -> Path:
    """Total -> each program's coverage -> family share, as a clean PNG."""
    out = waterfall_path(total_cents, covered, family_share_cents, out_dir)
    if out.exists():
        return out
    out_dir.mkdir(parents=True, exist_ok=True)
    labels = ["Plan total"] + [n for n, _ in covered] + ["Family share"]
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    running = total_cents / 100
    ax.bar(0, running, color="#2f6f5e")
    ax.text(0, running, f"${running:,.0f}", ha="center", va="bottom", fontsize=10)
    for i, (_, cents) in enumerate(covered, 1):
        amt = cents / 100
        running -= amt
        ax.bar(i, amt, bottom=running, color="#8fb8a8")
        ax.text(i, running + amt, f"-${amt:,.0f}", ha="center", va="bottom", fontsize=10)
    last = len(labels) - 1
    ax.bar(last, family_share_cents / 100, color="#d98a2b")
    ax.text(last, family_share_cents / 100, f"${family_share_cents / 100:,.0f}", ha="center", va="bottom", fontsize=10)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels([textwrap.fill(l, 18) for l in labels], fontsize=8)
    ax.set_yticks([])
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.set_title("Who pays for what", fontsize=13, loc="left")
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


# Card images must be public https URLs: ROOTED_IMAGE_BASE_URL (repo root) + repo-relative path, e.g. assets/bathrooms/b1.png.
# With the variable unset (local tests only) file:// URLs are returned; ASI:One cannot load those.
def to_url(path: Path) -> str:
    base = os.getenv("ROOTED_IMAGE_BASE_URL")
    if base:
        return f"{base.rstrip('/')}/{path.relative_to(ROOT).as_posix()}"
    return path.resolve().as_uri()

"""Generate the committed demo assets with Gemini (run by hand, never during the demo).

  .venv/bin/python -m agents.visualizer.prerender b1 [b2 ...]   writes assets/fallback/<id>/after.png and marked.png
Requires VISUALIZER_GEMINI=1 and GOOGLE_API_KEY in the environment.
"""
import asyncio
import json
import io
import os
import sys

from dotenv import load_dotenv
from PIL import Image

from agents.visualizer import catalog, render

load_dotenv(render.ROOT / ".env", override=True)


USAGE = render.ROOT / "spikes" / "designer" / "usage.json"  # shared with the P0-C spike's call counter


def _bump() -> int:
    try:
        n = json.loads(USAGE.read_text())["calls"]
    except Exception:
        n = 0
    if n >= 25:
        sys.exit("call cap reached; ask the lead")
    USAGE.write_text(json.dumps({"calls": n + 1}))
    return n + 1


async def main(ids: list[str]) -> None:
    os.environ["VISUALIZER_GEMINI"] = "1"
    for bid in ids:
        before = render.BATHROOMS / f"{bid}.png"
        if (render.FALLBACK / bid / "after.png").exists():
            print(bid, "exists, skipped")
            continue
        n = _bump()
        print(f"call {n}/25")
        fixes = catalog.fallback_set(bid)
        data = await render.gemini_edit(before, fixes, bid)
        if not data:
            print(bid, "gemini returned nothing; stopping, nothing written")
            break
        out = render.FALLBACK / bid
        out.mkdir(parents=True, exist_ok=True)
        Image.open(io.BytesIO(data)).convert("RGB").save(out / "after.png")
        render.draw_markers(out / "after.png", out / "marked.png", bid, fixes)
        print(bid, "wrote", out)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or catalog.BATHROOM_IDS))

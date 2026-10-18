"""rooted-visualizer: before/after bathroom images with numbered hazard markers."""
import asyncio
import os

from uagents import Context

from agents.visualizer import render
from shared.models import RenderRequest, RenderResult, WaterfallRequest, WaterfallResult
from shared.runtime import chat_protocol, describe_only, make_agent

agent = make_agent("visualizer")
agent.include(
    chat_protocol(describe_only("I render before/after bathroom images for rooted-orchestrator. Talk to rooted-orchestrator to start.")),
    publish_manifest=True,
)


@agent.on_message(RenderRequest, replies=RenderResult)
async def on_render(ctx: Context, sender: str, req: RenderRequest):
    ctx.logger.info(f"[{req.session_id}] RenderRequest bathroom={req.bathroom_id} fixes={len(req.fix_item_ids)} photo={bool(req.photo_url)}")
    if req.photo_url:
        try:
            out = await render.render_photo(req.photo_url, req.fix_item_ids, req.session_id)
            result = RenderResult(session_id=req.session_id, bathroom_id=req.bathroom_id, before_url="", after_url=out["after"],
                                  marked_url=out["after"], cached=out["cached"], fix_item_ids=out["fix_item_ids"])
        except Exception as exc:  # PhotoError messages never contain the URL
            ctx.logger.error(f"[{req.session_id}] photo render failed: {exc if isinstance(exc, render.PhotoError) else type(exc).__name__}")
            result = RenderResult(session_id=req.session_id, bathroom_id=req.bathroom_id, before_url="", after_url="",
                                  marked_url="", error="photo_failed")
        await ctx.send(sender, result)
        return
    try:
        out = await render.render(req.bathroom_id, req.fix_item_ids, req.session_id)
        # Uploads can block, so keep them off the event loop.
        urls = await asyncio.to_thread(lambda: [render.to_url(out[k]) for k in ("before", "after", "marked")])
        result = RenderResult(
            session_id=req.session_id,
            bathroom_id=req.bathroom_id,
            before_url=urls[0],
            after_url=urls[1],
            marked_url=urls[2],
            cached=out["cached"],
            fix_item_ids=out["fix_item_ids"],
        )
    except Exception as exc:
        ctx.logger.error(f"[{req.session_id}] render failed: {type(exc).__name__}")
        result = RenderResult(
            session_id=req.session_id, bathroom_id=req.bathroom_id, before_url="", after_url="", marked_url="", error="render_failed"
        )
    await ctx.send(sender, result)


@agent.on_message(WaterfallRequest, replies=WaterfallResult)
async def on_waterfall(ctx: Context, sender: str, req: WaterfallRequest):
    ctx.logger.info(f"[{req.session_id}] WaterfallRequest programs={len(req.program_amounts)}")
    try:
        covered = [(p.name, p.amount_cents) for p in req.program_amounts]
        if os.getenv("ROOTED_IMAGE_BASE_URL"):
            # Only a committed chart is reachable; a missing one is better skipped than shown with wrong numbers.
            path = render.waterfall_path(req.total_cents, covered, req.family_share_cents)
            if not path.exists():
                raise FileNotFoundError("no pre-rendered chart for these numbers")
        else:
            path = await asyncio.to_thread(render.render_waterfall, req.total_cents, covered, req.family_share_cents)
        url = await asyncio.to_thread(render.to_url, path)
        result = WaterfallResult(session_id=req.session_id, chart_url=url)
    except Exception as exc:
        ctx.logger.error(f"[{req.session_id}] waterfall failed: {type(exc).__name__}")
        result = WaterfallResult(session_id=req.session_id, chart_url="", error="waterfall_failed")
    await ctx.send(sender, result)


if __name__ == "__main__":
    agent.run()

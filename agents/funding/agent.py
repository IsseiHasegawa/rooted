"""rooted-funding: deterministic grant and insurance benefit matching over data/programs.json."""
from uagents import Context

from agents.funding.engine import allocate
from shared.models import FundingRequest, FundingResult
from shared.runtime import chat_protocol, describe_only, make_agent

agent = make_agent("funding")
agent.include(
    chat_protocol(describe_only("I match home-modification grants and insurance benefits for rooted-orchestrator. Talk to rooted-orchestrator to start.")),
    publish_manifest=True,
)


@agent.on_message(FundingRequest, replies=FundingResult)
async def on_funding(ctx: Context, sender: str, req: FundingRequest):
    # Never log income or health answers.
    ctx.logger.info(f"[{req.session_id}] FundingRequest with {len(req.line_items)} items")
    try:
        result = allocate(req)
    except Exception as e:  # noqa: BLE001
        ctx.logger.error(f"[{req.session_id}] funding failed: {type(e).__name__}")
        result = FundingResult(session_id=req.session_id, error="Funding check failed; showing full price.")
    ctx.logger.info(f"[{req.session_id}] FundingResult matches={len(result.matches)} covered_cents={result.covered_cents}")
    await ctx.send(sender, result)


if __name__ == "__main__":
    agent.run()

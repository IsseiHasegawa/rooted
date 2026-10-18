"""rooted-risk: CDC STEADI fall-risk scoring and room-by-room hazards from the CDC Check for Safety checklist."""
import asyncio

from uagents import Context

from agents.risk.logic import (
    STEADI,
    hazard_fields,
    map_concerns_keywords,
    map_concerns_llm,
    score_steadi,
    top_hazards,
)
from shared.models import AssessRequest, AssessResult, Hazard
from shared.runtime import chat_protocol, describe_only, make_agent

agent = make_agent("risk")
agent.include(
    chat_protocol(describe_only("I score the CDC STEADI fall-risk screening for rooted-orchestrator. Talk to rooted-orchestrator to start.")),
    publish_manifest=True,
)


@agent.on_message(AssessRequest, replies=AssessResult)
async def on_assess(ctx: Context, sender: str, req: AssessRequest):
    # Answers and concerns are health data: log counts and outcomes only.
    ctx.logger.info(f"[{req.session_id}] AssessRequest received ({len(req.answers)} answers)")
    try:
        score, at_risk, fell = score_steadi(req.answers)
        matched = await asyncio.to_thread(map_concerns_llm, req.concerns)
        mapper = "asi1"
        if matched is None:
            matched, mapper = map_concerns_keywords(req.concerns), "keywords"
        hazards = [Hazard(**hazard_fields(h)) for h in top_hazards(matched)]
        result = AssessResult(session_id=req.session_id, score=score, at_risk=at_risk, fell_past_year=fell,
                              hazards=hazards, source_url=STEADI["source_url"])
        ctx.logger.info(f"[{req.session_id}] assessed: {len(hazards)} hazards, concerns mapped by {mapper}")
    except Exception as exc:  # never leave the orchestrator waiting on a crash
        ctx.logger.error(f"[{req.session_id}] assessment failed: {type(exc).__name__}")
        result = AssessResult(session_id=req.session_id, score=0, at_risk=False, error="assessment_failed")
    await ctx.send(sender, result)


if __name__ == "__main__":
    agent.run()

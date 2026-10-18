"""rooted-risk: CDC STEADI fall-risk scoring and room-by-room hazards. P1 stub with fixed sample data."""
from uagents import Context

from shared.models import AssessRequest, AssessResult, Hazard
from shared.runtime import chat_protocol, describe_only, make_agent

STEADI_SOURCE = "https://www.cdc.gov/steadi/pdf/steadi-brochure-stayindependent-508.pdf"
SAFETY_SOURCE = "https://www.clemson.edu/cbshs/departments/prtm/_documents/check_for_safety_brochure-a.pdf"

agent = make_agent("risk")
agent.include(
    chat_protocol(describe_only("I score the CDC STEADI fall-risk screening for rooted-orchestrator. Talk to rooted-orchestrator to start.")),
    publish_manifest=True,
)


@agent.on_message(AssessRequest, replies=AssessResult)
async def on_assess(ctx: Context, sender: str, req: AssessRequest):
    ctx.logger.info(f"[{req.session_id}] AssessRequest received, answering with stub result")
    await ctx.send(
        sender,
        AssessResult(
            session_id=req.session_id,
            score=6,
            at_risk=True,
            fell_past_year=True,
            hazards=[
                Hazard(id="bath_no_grab_bar_tub", room="bathroom", label="No grab bar inside the tub", fix_item_ids=["grab_bar_tub"], source_url=SAFETY_SOURCE),
                Hazard(id="bath_no_grab_bar_toilet", room="bathroom", label="No grab bar next to the toilet", fix_item_ids=["grab_bar_toilet"], source_url=SAFETY_SOURCE),
                Hazard(id="path_dark", room="bed_to_bath", label="Dark path from bed to bathroom", fix_item_ids=["night_light"], source_url=SAFETY_SOURCE),
            ],
            source_url=STEADI_SOURCE,
        ),
    )


if __name__ == "__main__":
    agent.run()

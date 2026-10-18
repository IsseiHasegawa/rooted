"""rooted-orchestrator: the only agent ASI:One talks to. Session state, cards, dispatching, Stripe seller.

P1 stub: answers every chat message with a placeholder intake card.
"""
from uagents import Context
from uagents_core.contrib.protocols.chat import ChatMessage

from shared.cards import card_message
from shared.runtime import chat_protocol, make_agent, message_text

agent = make_agent("orchestrator")


async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
    session_id = str(ctx.session)
    ctx.logger.info(f"[{session_id}] chat received ({len(message_text(msg))} chars), state=STUB -> sending intake card")
    await ctx.send(
        sender,
        card_message(
            "Hi, I'm Rooted. I help families make an older parent's home safer so they can stay in it. Let's start with a few questions.",
            "form",
            {"title": "About your parent", "fields": [], "cta": {"label": "Continue", "selection": {"action": "submit_intake", "session_id": session_id}}},
        ),
    )


agent.include(chat_protocol(on_chat), publish_manifest=True)


if __name__ == "__main__":
    agent.run()

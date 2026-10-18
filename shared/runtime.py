"""Agent bootstrap shared by every Rooted agent: registry, seeds, addresses, chat protocol."""
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol
from uagents.resolver import GlobalResolver, Resolver
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.identity import Identity

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# key -> Agentverse name, seed variable, local port, module; readme and description go to the Agentverse profile
AGENTS: dict[str, dict] = {
    "orchestrator": {"name": "rooted-orchestrator", "seed_env": "ORCHESTRATOR_SEED", "port": 8000, "module": "agents.orchestrator.agent"},
    "risk": {"name": "rooted-risk", "seed_env": "RISK_SEED", "port": 8001, "module": "agents.risk.agent"},
    "visualizer": {"name": "rooted-visualizer", "seed_env": "VISUALIZER_SEED", "port": 8002, "module": "agents.visualizer.agent"},
    "funding": {"name": "rooted-funding", "seed_env": "FUNDING_SEED", "port": 8003, "module": "agents.funding.agent"},
    "juniper": {"name": "contractor-juniper", "seed_env": "CONTRACTOR_JUNIPER_SEED", "port": 8004, "module": "agents.contractor.agent", "env": {"CONTRACTOR_ID": "juniper"}},
    "mesa": {"name": "contractor-mesa", "seed_env": "CONTRACTOR_MESA_SEED", "port": 8005, "module": "agents.contractor.agent", "env": {"CONTRACTOR_ID": "mesa"}},
    "arbor": {"name": "contractor-arbor", "seed_env": "CONTRACTOR_ARBOR_SEED", "port": 8006, "module": "agents.contractor.agent", "env": {"CONTRACTOR_ID": "arbor"}},
}
CONTRACTOR_KEYS = ("juniper", "mesa", "arbor")


def seed_for(key: str) -> str:
    seed_env = AGENTS[key]["seed_env"]
    seed = os.getenv(seed_env)
    if not seed:
        raise RuntimeError(f"{seed_env} is missing from .env. Run: .venv/bin/python scripts/ensure_seeds.py")
    return seed


def address_of(key: str) -> str:
    # Same derivation uagents uses for Agent(seed=...), so no agent has to be running.
    return Identity.from_seed(seed_for(key), 0).address


class LocalFirstResolver(Resolver):
    """Deliver to Rooted agents on this machine directly over their local /submit endpoint.

    Mailboxes are only needed for ASI:One to reach the orchestrator; the specialists talk over
    localhost, so they work without an Agentverse mailbox slot and without the Almanac round trip.
    Every other address (ASI:One users, sim client) goes through the normal global resolver.
    """

    def __init__(self):
        self._local = {address_of(k): [f"http://127.0.0.1:{s['port']}/submit"] for k, s in AGENTS.items()}
        self._global = GlobalResolver()

    async def resolve(self, destination: str) -> tuple[str | None, list[str]]:
        address = destination.split("://")[-1].split("/")[-1]
        if address in self._local:
            return address, self._local[address]
        return await self._global.resolve(destination)


DESCRIPTIONS = {
    "orchestrator": "Chat with this agent in ASI:One to make an older parent's bathroom safer: fall-risk check, fixes, quotes, funding and a Stripe deposit.",
    "risk": "Scores the CDC STEADI fall-risk screening and lists home hazards for rooted-orchestrator.",
    "visualizer": "Draws the safer bathroom with numbered fix markers and the cost waterfall for rooted-orchestrator.",
    "funding": "Matches grants and insurance benefits with deterministic rules and sources for rooted-orchestrator.",
    "juniper": "Juniper Home Repair: a fictional, lowest-price contractor that quotes home-safety fixes for rooted-orchestrator.",
    "mesa": "Mesa Handyworks: a fictional, fastest-start contractor that quotes home-safety fixes for rooted-orchestrator.",
    "arbor": "Arbor CAPS Remodeling: a fictional, CAPS-certified contractor that quotes home-safety fixes for rooted-orchestrator.",
}


def readme_path(key: str) -> Path:
    return ROOT / "agents" / ("contractor" if key in CONTRACTOR_KEYS else key) / "README.md"


def make_agent(key: str) -> Agent:
    spec = AGENTS[key]
    return Agent(
        name=spec["name"],
        seed=seed_for(key),
        port=int(os.getenv("AGENT_PORT", spec["port"])),
        mailbox=True,
        publish_agent_details=True,
        # Sent to Agentverse on /connect (scripts/connect_mailboxes.py).
        readme_path=str(readme_path(key)),
        description=DESCRIPTIONS[key],
        resolve=LocalFirstResolver(),
    )


def text_message(text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=text)],
    )


def message_text(msg: ChatMessage) -> str:
    return " ".join(c.text for c in msg.content if isinstance(c, TextContent) and c.text).strip()


ChatHandler = Callable[[Context, str, ChatMessage], Awaitable[None]]


def chat_protocol(on_chat: ChatHandler) -> Protocol:
    proto = Protocol(spec=chat_protocol_spec)

    @proto.on_message(ChatMessage)
    async def _on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(
            sender,
            ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id),
        )
        await on_chat(ctx, sender, msg)

    @proto.on_message(ChatAcknowledgement)
    async def _on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        return

    return proto


def describe_only(description: str) -> ChatHandler:
    """Chat handler for specialist agents that ASI:One may discover but should not drive directly."""

    async def _handler(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, text_message(description))

    return _handler


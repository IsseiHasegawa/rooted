"""rooted-orchestrator: the only agent ASI:One talks to. Session state, cards, dispatching, Stripe seller."""
from agents.orchestrator import billing
from agents.orchestrator.flow import handle_chat
from shared.runtime import chat_protocol, make_agent

agent = make_agent("orchestrator")
agent.include(chat_protocol(handle_chat), publish_manifest=True)
agent.include(billing.protocol(), publish_manifest=True)


if __name__ == "__main__":
    agent.run()

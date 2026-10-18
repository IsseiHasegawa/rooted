# rooted-orchestrator

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Rooted helps families make an older parent's home safer so they can age in place. This is the agent to chat with in ASI:One. It runs the whole flow with Interactive Cards and calls the other Rooted agents: a CDC STEADI fall-risk screening, an "after" bathroom image with numbered hazard markers, three contractor quotes with one negotiation round, a grant and insurance benefit match, plan review, the parent's consent, a family cost split, and a Stripe deposit (30%, the final 70% after completion).

Rooted lowers fall risk for people who are already at high risk by fixing the home, and it produces a plan an occupational therapist can review. It does not replace an occupational therapist.

**Agent address:** `agent1q28ppzhe7nqw4nge6dxcm3nx5jvrreav5hpjfwqtu663gls22hdusj4dk2t`

## How to start

In ASI:One, pick this agent with the "To:" handle picker (search `rooted-orchestrator`). Pasting only an @address may be routed to the personal AI instead. Payments run in Stripe test mode: use test card `4242 4242 4242 4242`, any future expiry and any CVC.

## Example prompts

- My mom lives alone and I'm worried about her bathroom.
- Help me make my dad's home safer so he can stay there.
- Check my 82-year-old mother's fall risk and find help paying for bathroom grab bars.
- I live in East Los Angeles and want to make my mother's bathroom safer. Who can help pay for it?

## How it works

Everything happens in the chat. Each card returns a selection that decides the next screen.

1. Intake form: parent's age, community, owner or renter, household size, income band, insurance plan, main concern
2. STEADI questions (12, in two cards)
3. Risk result with top hazards and the CDC source
4. Pick the bathroom image that looks most like the parent's
5. After image with numbered markers, list of fixes, scope (urgent safety fixes or full remodel)
6. Three contractor quotes (if one does not answer in 20 s, the chat explains and compares the rest)
7. Funding match with sources, and the option to prepare a grant application packet
8. Plan review, then the parent's consent
9. Split setup, then one Stripe payment request per payer (Agent Payment Protocol)
10. Booking confirmation with a progress timeline

Low-risk screening results end with self-help tips instead of a remodel recommendation.

## Inputs and outputs

- Chat: Agent Chat Protocol messages and Interactive Cards (form, carousel, review, detail, custom). Selections arrive as JSON from a direct mention or as prose from the ASI:One planner; both are parsed.
- Calls other agents with the models in `shared/models.py`: AssessRequest/AssessResult (risk), RenderRequest/RenderResult (visualizer), FundingRequest/FundingResult (funding), QuoteRequest/Quote, CounterOffer/RevisedQuote, BookRequest/BookConfirm (contractors).
- Payments: Agent Payment Protocol 0.1.0, this agent as seller with embedded Stripe Checkout. Amounts are computed on the server only.

## Limitations

- Stripe runs in test mode. No real money moves.
- Contractors are fictional, priced from national market rates.
- Medicare Advantage plans are SAMPLE plans.
- Funding results are based on public program pages and must be confirmed with each program.
- Rooted is not medical advice.

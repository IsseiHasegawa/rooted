# Rooted: Devpost draft

## Tagline
Make an older parent's home safer, inside one ASI:One chat.

## Inspiration
Most older adults want to stay in their own homes, and the bathroom is where many falls happen. Families who want to help face a maze: is my parent at risk, what should change, who can do the work, and what can we get paid for. Rooted turns that maze into one conversation.

## What it does
- Screens the parent with the CDC STEADI 12-question check.
- Shows the bathroom after the fixes, with numbered hazard markers.
- Collects three contractor quotes and runs one negotiation round. If a contractor does not answer within 20 seconds, the flow continues with the others.
- Matches the family to LACDA, NHS LA County, PICF HUD and Medicare Advantage home-safety benefits with deterministic rules. Each result shows its source page.
- Produces an OT-ready plan, asks the parent for consent, splits the 30% deposit across family members with Stripe, and books the work. The final 70% is paid after completion.

Rooted does not replace an occupational therapist. Home modifications lower fall rates for people at high risk, and Rooted gets the family to an OT-ready plan faster.

## How we built it
Seven uAgents on Agentverse: an orchestrator, risk, visualizer, funding and three contractor agents. The chat runs on the Agent Chat Protocol with ASI:One Interactive Cards. The orchestrator is the seller in the Agent Payment Protocol with embedded Stripe Checkout, and amounts are computed server-side only. Funding eligibility is plain Python over a JSON file where every record has a source URL and date, so no LLM decides who qualifies. Gemini renders the after images, with cached fallbacks.

Built with: uAgents, Agentverse, ASI:One, Agent Chat Protocol, Payment Protocol, Interactive Cards, Stripe, Gemini.

## How funding stays trustworthy
Eligibility is Python over a JSON file, with unit tests for a homeowner in East Los Angeles and a renter in Pasadena. LACDA and PICF rules were checked against their pages. Where we could not verify a fact (NHS, the CMS memo), the data says so and the family is told to confirm with the program.

## Try it
Open the shared ASI:One chat (link below) and say: "My mom lives alone and I'm worried about her bathroom." Use the demo parent: age 82, East Los Angeles, owner, one person living at home.

## Challenges
- Card selections arrive as JSON from a direct mention and as prose from the ASI:One planner, so every selection parser handles both.
- Keeping funding trustworthy: rules live in data with sources, and unknown limits are labeled rather than guessed.
- Never blocking on one agent: contractor calls use timeouts and partial results.

## Accomplishments
- A complete family flow, from first message to Stripe deposit, with no custom frontend.
- Eligibility decisions a judge can audit line by line.

## What we learned
Agents are most useful when each one has a narrow job and a clear message contract.

## What's next
- Real Medicare Advantage plan data and local ZIP-level prices.
- Real contractors and OT referrals.
- More rooms than the bathroom.

## Honest limits
Medicare Advantage plans are SAMPLE plans, contractors are fictional (priced from national market rates), payments run in Stripe test mode, and Rooted does not check the LACDA single-family, condominium or townhome requirement. Rooted is not medical advice and does not replace an occupational therapist.

## Links
- GitHub: TODO
- Demo video (3-5 min): TODO
- ASI:One chat share URL: TODO
- Agentverse agent URL: TODO
- Devpost URL and tracks: TODO (confirm multi-track entry with organizers)

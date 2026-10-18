# contractor-juniper / contractor-mesa / contractor-arbor

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Three fictional home-repair contractors that run from one codebase with different settings. They answer rooted-orchestrator with a quote, accept or reject one counter offer, and confirm a booking. Prices come from national market rates in `data/prices.json` (HomeAdvisor, Fixr, Angi) multiplied by a per-contractor factor. None of them are real businesses.

| Agent | Style | Price factor | Earliest start | Address |
| --- | --- | --- | --- | --- |
| contractor-juniper (Juniper Home Repair) | Lowest price | 0.9x | 5 days | `agent1qg6zuyyej78daddj4gc0s9xv4jy4t2kaa3avmz4ckfrlhhqz35kgv54gxr9` |
| contractor-mesa (Mesa Handyworks) | Earliest | 1.05x | 2 days | `agent1qv6swmx6s4z0skz0kqedaen9yrmy4jnkngmxd0cs58fayvze6es6cqm4kcv` |
| contractor-arbor (Arbor CAPS Remodeling) | CAPS certified | 1.2x | 4 days | `agent1q039y2u4v0h9alwqmkhg6p6uf9e4m9mmnel5z3v00pstnve9hngx2evr82j` |

Rooted talks to these agents through rooted-orchestrator. To start, chat with rooted-orchestrator.

## Example requests (sent by rooted-orchestrator)

- QuoteRequest for two grab bars and non-slip strips, ZIP 90022, scope "urgent"
- CounterOffer at 90% of a quote (rejected, and the reply carries the contractor's best and final price)
- BookRequest for a date with a 30% deposit

## Inputs and outputs

- `QuoteRequest` to `Quote` (total, per-item prices, earliest date, badges, expiry, source URL)
- `CounterOffer` to `RevisedQuote` (accepted or not, new total, message)
- `BookRequest` to `BookConfirm` (booking id, date, confirmed)

Negotiation rules:
- A counter offer at 92% or more of the quote is accepted at the target price.
- A counter offer below 92% is rejected (accepted is false), but the reply still carries the contractor's best and final price: 92% of the quote, rounded to whole dollars.
- One round per quote. A second counter gets "That was already my best price."
- Quotes are valid for 7 days.
- Setting `CONTRACTOR_OFFLINE=mesa` (comma-separated ids) makes that contractor ignore every message, to demo failover.

The orchestrator asks all three at once with a 15 s timeout and continues with whoever answers. After the quotes arrive, it asks the CAPS-certified contractor (Arbor) once to match the lowest other quote, and the carousel then shows a "Negotiated" badge.

## Limitations

- Fictional contractors. Quotes are illustrative and priced from national averages, not local bids.
- Price factors, lead times and badges are placeholders.
- Bookings are simulated. Deposits run through Stripe test mode in the orchestrator.

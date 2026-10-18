# contractor-juniper / contractor-mesa / contractor-arbor

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Three fictional home-repair contractors that run from one codebase with different settings. They answer rooted-orchestrator with a quote, accept or reject one counter offer, and confirm a booking (per P7, in progress; the agents currently return stub answers). Prices come from national market rates in `data/prices.json` (HomeAdvisor, Fixr, Angi) multiplied by a per-contractor factor. None of them are real businesses.

| Agent | Style | Price factor | Earliest start | Address |
| --- | --- | --- | --- | --- |
| contractor-juniper (Juniper Home Repair) | Lowest price | 0.9x | 5 days | `agent1qtgg2ms6z833m942hemksv356u458s36l0rtxgpahhp9yep3zwejqz2xny3` |
| contractor-mesa (Mesa Handyworks) | Earliest | 1.05x | 2 days | `agent1qdj37u97wqftp6sprrl0rnem4xqwgsd7yzkdvz7dnr7tjxph7ju3j2l8qyt` |
| contractor-arbor (Arbor CAPS Remodeling) | CAPS certified | 1.2x | 4 days | `agent1qf396us3gl42yrw3n935fk5h0wwztmern0r8z9mtdm6e86jsv0k456g0phj` |

Rooted talks to these agents through rooted-orchestrator. To start, chat with rooted-orchestrator.

## Example requests (sent by rooted-orchestrator)

- QuoteRequest for two grab bars and non-slip strips, ZIP 90022, scope "urgent"
- CounterOffer at 90% of a quote (accepted at 92% or more of the quote)
- BookRequest for a date with a 30% deposit

## Inputs and outputs

- `QuoteRequest` to `Quote` (total, per-item prices, earliest date, badges, expiry, source URL)
- `CounterOffer` to `RevisedQuote` (accepted or not, new total, message)
- `BookRequest` to `BookConfirm` (booking id, date, confirmed)

The orchestrator asks all three at once with a 20 s timeout and continues with whoever answers.

## Limitations

- Fictional contractors. Quotes are illustrative and priced from national averages, not local bids.
- Price factors, lead times and badges are placeholders.
- Bookings are simulated. Deposits run through Stripe test mode in the orchestrator.

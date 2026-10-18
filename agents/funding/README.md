# rooted-funding

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Matches an older adult's situation to home-modification grants and insurance benefits with deterministic Python rules over `data/programs.json`. No LLM decides who qualifies. Every program and result carries its source URL and date. Programs covered: LACDA Senior Grant Program, NHS LA County Senior Minor Home Repair Grant, PICF HUD Older Adults Home Modification Program, and two SAMPLE Medicare Advantage plans with a supplemental safety benefit.

**Agent address:** `agent1qgzdppjh8ga6hng8cwgpuvxt4gh5nahw757jp6enr77qy2zpjrkxcvnevpr`

Rooted talks to this agent through rooted-orchestrator. To start, chat with rooted-orchestrator.

## Example requests (sent by rooted-orchestrator)

- FundingRequest: age 82, East Los Angeles, owner for 20 years, 1 person, grab bars, non-slip strips and tub-to-shower conversion
- FundingRequest: age 78, Pasadena, renter, 2 people (no LACDA match, PICF only)

## Inputs and outputs

- Input: `FundingRequest` (session_id, age, community, tenure, months_owned, household_size, annual_income in USD, insurance_plan, line_items)
- Output: `FundingResult` (matches with amount_cents, covered_item_ids, source_url, as_of, next_step and funded_later; total, covered and family share in cents; pay_now and funded_later item ids; error)

Items that a grant would pay are marked "funded later" because the money arrives only after an application. Items covered by a plan benefit or by the family can be paid now. Income is never logged.

## Limitations

- Medicare Advantage plans are SAMPLE plans with invented allowances.
- The NHS LA County income limit is not published, so Rooted uses the LACDA 80% table as a labeled proxy. NHS and CMS facts are not yet verified on the source pages.
- LACDA also requires a single-family home, condominium or townhome. This agent does not check property type.
- Rules come from public pages and can change. Applicants must confirm with each program. This is not legal or financial advice.

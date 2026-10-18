# rooted-risk

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Scores the CDC STEADI "Stay Independent" fall-risk screening (12 questions) and lists room-by-room home hazards from the CDC "Check for Safety" checklist, with the fix items that address each hazard. Scoring is plain Python from `data/steadi.json`. Free-text concerns are matched to checklist hazards by ASI:One (asi1-mini, strict schema, known ids only), with a keyword fallback. The LLM never touches the score. At risk means a score of 4 or more, or a score under 4 with a fall in the past year.

**Agent address:** `agent1qg22mcxxwxkpvchv7nxpjtgwt76tzme4xwzdxxzzamxksye644w3uz0enkj`

Rooted talks to this agent through rooted-orchestrator. To start a full screening, chat with rooted-orchestrator.

## Example requests (sent by rooted-orchestrator)

- AssessRequest with the 12 yes/no answers and "slippery tub, gets up at night"
- AssessRequest with all answers "no" (at_risk false; the orchestrator then shows self-help tips)

## Inputs and outputs

- Input: `AssessRequest` (session_id, answers by STEADI question id, free-text concerns)
- Output: `AssessResult` (score, at_risk, fell_past_year, hazards with room, label, fix item ids and source URL, source_url, error)

Sources: [CDC STEADI Stay Independent](https://www.cdc.gov/steadi/pdf/STEADI-Brochure-StayIndependent-508.pdf), [CDC STEADI algorithm](https://www.cdc.gov/steadi/media/pdfs/STEADI-Algorithm-508.pdf), [CDC Check for Safety](https://www.clemson.edu/cbshs/departments/prtm/_documents/check_for_safety_brochure-a.pdf).

## Limitations

- A self-screening tool, not a diagnosis. It does not replace an occupational therapist or a clinician.
- Health answers are used only for the session and are not logged.
- Returns the top 3 hazards for the bathroom and the bed-to-bath path.

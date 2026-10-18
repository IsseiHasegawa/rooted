# rooted-visualizer

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Renders a before and after image of a bathroom with numbered markers for each safety fix, and the cost waterfall chart (total, covered by each program, family share). Uses a Gemini image model with cached demo images as a fallback, so the flow never waits on image generation.

**Agent address:** `agent1qdy4lckmkyme8n2y934ertq30tmxd4d8m7e4pfq8vulcjgqska7hgdygda4`

Rooted talks to this agent through rooted-orchestrator. To start, chat with rooted-orchestrator.

## Example requests (sent by rooted-orchestrator)

- RenderRequest for bathroom b1 with grab bars, non-slip strips and a night light

## Inputs and outputs

- Input: `RenderRequest` (session_id, bathroom_id, fix_item_ids; marker numbers follow that order)
- Output: `RenderResult` (before_url, after_url, marked_url, cached, error)

## Limitations

- After images are AI-generated illustrations of the proposed fixes, not a guarantee of how the finished work will look.
- Bathroom images come from a small set of representative photos.
- Image URLs are full URLs built from `ROOTED_IMAGE_BASE_URL` (https mode); an `agent-storage` mode exists for Agentverse storage. The scheme in production is pending the card image test.

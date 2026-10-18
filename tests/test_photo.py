"""The family's own bathroom photo: link extraction, download limits, budget, and the fallback to the picker."""
import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.orchestrator import flow  # noqa: E402
from agents.visualizer import render  # noqa: E402
from shared.models import RenderResult  # noqa: E402

CLOUDINARY = "https://res.cloudinary.com/demo/image/upload/v1/chat/abc123.jpg"


def test_photo_url_from_asi_one_text():
    text = f"The user attached an image: {CLOUDINARY}. They say: this is her bathroom"
    assert flow.photo_url_in(text) == CLOUDINARY
    assert flow.photo_url_in(f"see ({CLOUDINARY})") == CLOUDINARY
    assert flow.photo_url_in("photo at https://example.org/pics/bath.PNG please") == "https://example.org/pics/bath.PNG"
    # Not a photo: other links, plain http, no link at all.
    assert flow.photo_url_in("source https://www.cdc.gov/steadi/index.html") is None
    assert flow.photo_url_in("http://res.cloudinary.com/x/a.jpg") is None
    assert flow.photo_url_in("my mom's bathroom") is None


def _response(content_type: str, chunks: list[bytes]):
    r = mock.MagicMock()
    r.__enter__.return_value = r
    r.headers = {"Content-Type": content_type}
    r.iter_content.return_value = chunks
    return r


def test_download_accepts_images_only_and_caps_size():
    with mock.patch.object(render.requests, "get", return_value=_response("image/jpeg", [b"x" * 10])):
        assert render.download_photo(CLOUDINARY) == b"x" * 10
    with mock.patch.object(render.requests, "get", return_value=_response("text/html", [b"<html>"])):
        with pytest.raises(render.PhotoError, match="not an image"):
            render.download_photo(CLOUDINARY)
    big = [b"x" * (1024 * 1024)] * 11
    with mock.patch.object(render.requests, "get", return_value=_response("image/png", big)):
        with pytest.raises(render.PhotoError, match="10 MB"):
            render.download_photo(CLOUDINARY)
    with pytest.raises(render.PhotoError):
        render.download_photo("http://res.cloudinary.com/a.jpg")
    with mock.patch.object(render.requests, "get", side_effect=render.requests.Timeout()) as get:
        with pytest.raises(render.PhotoError) as e:
            render.download_photo(CLOUDINARY)
    assert get.call_args.kwargs["timeout"] == 10 and "cloudinary" not in str(e.value)


def test_budget_counts_each_call_and_stops_at_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "BUDGET_FILE", tmp_path / "budget.json")
    monkeypatch.setattr(render, "GEMINI_CALL_CAP", 21)
    render.spend_gemini_call()  # 19 already spent before tracking -> 20
    render.spend_gemini_call()  # 21
    with pytest.raises(render.PhotoError, match="budget"):
        render.spend_gemini_call()


def test_gemini_failure_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "BUDGET_FILE", tmp_path / "budget.json")
    monkeypatch.setattr(render, "CACHE", tmp_path / "cache")
    monkeypatch.setenv("GOOGLE_API_KEY", "test")
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(buf, "PNG")
    with mock.patch.object(render, "download_photo", return_value=buf.getvalue()), \
         mock.patch.object(render, "_gemini_edit_sync", side_effect=RuntimeError("quota")) as gemini:
        with pytest.raises(render.PhotoError, match="gemini failed"):
            asyncio.run(render.render_photo(CLOUDINARY, ["grab_bar_tub"]))
    assert gemini.call_count == 1
    assert '"used": 20' in (tmp_path / "budget.json").read_text()


def test_edited_photo_fits_in_a_card():
    noisy = Image.effect_noise((2000, 1500), 80).convert("RGB")  # worst case for JPEG size
    uri = render.to_data_uri(noisy)
    assert uri.startswith("data:image/jpeg;base64,") and len(uri) <= render.PHOTO_DATA_URI_MAX


class Ctx:
    def __init__(self):
        self.store: dict = {}
        self.storage = SimpleNamespace(get=self.store.get, set=self.store.__setitem__)
        self.logger = mock.Mock()
        self.session = "chat"
        self.sent: list = []

    async def send(self, to, msg):
        self.sent.append(msg)


def photo_session(ctx: Ctx) -> dict:
    s = {"session_id": "s-photo", "sender": "agent1user", "state": "PICK_BATHROOM", "card": None,
         "data": {"intake": {"parent_name": "Maria"}, "photo_url": CLOUDINARY,
                  "risk": {"hazards": [{"fix_item_ids": ["non_slip_mat", "night_light", "grab_bar_tub"]}]}}}
    flow.save(ctx, s)
    return s


@pytest.mark.parametrize("reply", [None, RenderResult(session_id="s-photo", bathroom_id="photo", before_url="",
                                                      after_url="", marked_url="", error="photo_failed")])
def test_photo_failure_falls_back_to_the_picker(reply):
    ctx = Ctx()
    s = photo_session(ctx)
    with mock.patch.object(flow, "call", mock.AsyncMock(return_value=reply)):
        asyncio.run(flow.use_photo(ctx, s))
    s = flow.load(ctx, "s-photo")
    assert s["state"] == "PICK_BATHROOM" and "photo_url" not in s["data"]
    assert s["card"]["narration"] == flow.PHOTO_FAILED
    assert any(b["children"][2]["action"]["selection"]["action"] == "pick_bathroom" for b in s["card"]["payload"]["root"]["children"])


def test_photo_success_sends_the_picture_then_the_fix_list():
    from uagents_core.contrib.protocols.chat import ResourceContent
    ctx = Ctx()
    s = photo_session(ctx)
    ok = RenderResult(session_id="s-photo", bathroom_id="photo", before_url="", after_url="data:image/jpeg;base64,AAAA",
                      marked_url="data:image/jpeg;base64,AAAA", fix_item_ids=["non_slip_mat", "night_light", "grab_bar_tub"])
    resource = flow.ChatMessage(timestamp=flow.datetime.now(flow.timezone.utc), msg_id=flow.uuid4(), content=[ResourceContent(
        type="resource", resource_id=flow.uuid4(), resource={"uri": "agent-storage://x/1", "metadata": {"mime_type": "image/jpeg"}})])
    with mock.patch.object(flow, "call", mock.AsyncMock(return_value=ok)), \
         mock.patch.object(flow, "upload_photo", return_value=resource) as upload:
        asyncio.run(flow.use_photo(ctx, s))
    assert upload.call_args.args[1] == b"\x00\x00\x00" and upload.call_args.args[2] == "agent1user"  # readable by this chat only
    assert ctx.sent[-2] is resource  # the picture as its own message, just before the card
    s = flow.load(ctx, "s-photo")
    card = s["card"]["payload"]["root"]
    assert s["state"] == "RENDER" and card["title"] == "Your bathroom, made safer"
    assert "image" not in json_dump(card)
    # Worded for any layout, toilet bar added, in this order, whatever order the hazards came in.
    assert [i["children"][0]["value"] for i in card["children"][0]["items"]] == [
        "Fix 1: Grab bar in the tub or shower", "Fix 2: Slip-resistant surface in the tub or shower",
        "Fix 3: Grab bar next to the toilet", "Fix 4: Motion night lights on the bed-to-bath path"]
    texts = [flow.message_text(m) for m in ctx.sent if not getattr(m, "content", [None])[0].__class__.__name__ == "ResourceContent"]
    assert flow.PHOTO_SHOWN in texts
    assert flow.PHOTO_SHOWN == "Here is your bathroom with these fixes (AI preview; the contractor confirms on site)."
    assert card["children"][-1]["action"]["selection"]["action"] == "get_quotes"


def test_upload_failure_falls_back_to_the_picker():
    ctx = Ctx()
    s = photo_session(ctx)
    ok = RenderResult(session_id="s-photo", bathroom_id="photo", before_url="", after_url="data:image/jpeg;base64,AAAA",
                      marked_url="data:image/jpeg;base64,AAAA")
    with mock.patch.object(flow, "call", mock.AsyncMock(return_value=ok)), \
         mock.patch.object(flow, "upload_photo", side_effect=RuntimeError("Asset creation failed: 500")):
        asyncio.run(flow.use_photo(ctx, s))
    s = flow.load(ctx, "s-photo")
    assert s["state"] == "PICK_BATHROOM" and s["card"]["narration"] == flow.PHOTO_FAILED


def json_dump(obj) -> str:
    import json
    return json.dumps(obj)


def test_preset_after_card_says_it_is_a_similar_bathroom():
    from shared.cards import bathroom_pick_card
    sub = bathroom_pick_card("s", [{"id": "b1", "label": "Tub"}])[1]["root"]["subtitle"]
    assert sub.endswith("Or attach a photo of the bathroom with the + button.")


def test_photo_in_the_first_message_is_kept_for_the_bathroom_step():
    from shared.runtime import text_message
    ctx = Ctx()
    asyncio.run(flow.handle_chat(ctx, "agent1user", text_message(f"The user attached an image: {CLOUDINARY} help my mom")))
    sid = ctx.store[flow._active_key("agent1user")]
    s = flow.load(ctx, sid)
    assert s["state"] == "INTAKE" and s["data"]["photo_url"] == CLOUDINARY
    # Reaching the bathroom step uses the photo instead of the picker.
    s["data"].update(intake={"parent_name": "Maria"}, risk={"hazards": []})
    s["state"] = "RISK_RESULT"
    flow.save(ctx, s)
    with mock.patch.object(flow, "use_photo", mock.AsyncMock()) as use_photo:
        asyncio.run(flow.on_continue(ctx, s, {"action": "continue"}))
    use_photo.assert_awaited_once()
    # The URL itself is never logged.
    assert not any("cloudinary.com/demo" in str(c) for c in ctx.logger.info.call_args_list)


def test_photo_prompt_fits_any_layout():
    prompt = render.photo_prompt(flow.photo_fix_ids(["grab_bar_shower", "night_light"]))
    assert prompt.startswith("Edit this photo of the bathroom. Add these to the existing tub or shower, toilet and path; "
                             "keep everything else identical:")
    assert "grab bar next to the toilet" in prompt and prompt.count("tub or shower") == 3


def test_photo_quote_items_use_the_photo_wording():
    items = flow.line_items(flow.photo_fix_ids([]), flow.PHOTO_LABELS)
    assert [i.label for i in items][:2] == ["Grab bar in the tub or shower", "Slip-resistant surface in the tub or shower"]

"""Teams delivery: card shape, retry behaviour and credential handling."""

from datetime import datetime

import httpx
import pytest

from cns import notify
from cns.scoring import Score


class FakeHeadline:
    id = 7
    title = "Iran threatens to close the Strait of Hormuz"
    category = "geo_risk"
    relevance_terms = "hormuz,iran,strait"
    link = "https://example.invalid/news/1"
    published_at = datetime(2026, 8, 26, 12, 0, 0)


def card_of(payload):
    return payload["attachments"][0]["content"]


def texts(payload):
    return [b.get("text", "") for b in card_of(payload)["body"]]


@pytest.fixture
def webhook(monkeypatch):
    monkeypatch.setattr(notify.settings, "teams_webhook_url", "https://flow.invalid/hook")
    monkeypatch.setattr(notify.settings, "teams_enabled", True)
    monkeypatch.setattr(notify.settings, "teams_bearer_token", "")
    monkeypatch.setattr(notify.settings, "teams_tenant_id", "")
    monkeypatch.setattr(notify, "_token", None)


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(notify.time, "sleep", lambda _: None)


def responder(*statuses):
    """A POST stub yielding the given statuses in order, recording each call."""
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        status = statuses[min(len(calls) - 1, len(statuses) - 1)]
        return httpx.Response(status, text="body", request=httpx.Request("POST", url))

    fake_post.calls = calls
    return fake_post


# --- card shape -----------------------------------------------------------


def test_payload_is_an_adaptive_card_envelope():
    """The Workflows template rejects any body that is not a card.

    A plain {"text": ...} passes the trigger with a 202 and then fails the run,
    which is exactly how this was discovered.
    """
    payload = notify.build_payload(FakeHeadline())
    assert payload["type"] == "message"
    assert payload["attachments"][0]["contentType"] == (
        "application/vnd.microsoft.card.adaptive"
    )
    assert card_of(payload)["type"] == "AdaptiveCard"


def test_unscored_card_is_a_sender_line_and_the_headline():
    body = card_of(notify.build_payload(FakeHeadline()))["body"]
    assert len(body) == 2
    assert body[0]["text"] == "_Siddharth Raj:_"
    assert body[1]["text"] == FakeHeadline.title


def test_sender_line_is_grey_and_italic():
    """isSubtle greys it; the underscores are markdown italics."""
    sender = card_of(notify.build_payload(FakeHeadline()))["body"][0]
    assert sender["isSubtle"] is True
    assert sender["text"].startswith("_") and sender["text"].endswith("_")


def test_headline_wraps():
    """Headlines run long; without wrap they are truncated in the chat."""
    for block in card_of(notify.build_payload(FakeHeadline()))["body"]:
        assert block["wrap"] is True


def test_no_button_no_category_no_facts():
    card = card_of(notify.build_payload(FakeHeadline()))
    assert "actions" not in card
    assert not [b for b in card["body"] if b["type"] == "FactSet"]
    assert "GEOPOLITICS" not in texts(notify.build_payload(FakeHeadline()))


def test_scored_card_adds_one_score_line():
    payload = notify.build_payload(
        FakeHeadline(), score=Score(72.0, "bullish", 0.8, "supply_down")
    )
    assert texts(payload) == ["_Siddharth Raj:_", FakeHeadline.title, "BULLISH  +72"]


def test_bullish_and_bearish_are_coloured_differently():
    bull = notify.build_payload(FakeHeadline(), score=Score(72.0, "bullish", 0.8))
    bear = notify.build_payload(FakeHeadline(), score=Score(-60.0, "bearish", 0.7))
    assert card_of(bull)["body"][2]["color"] == "Good"
    assert card_of(bear)["body"][2]["color"] == "Attention"
    assert card_of(bear)["body"][2]["text"] == "BEARISH  -60"


def test_no_score_line_when_unscored():
    """A rendered zero would be indistinguishable from a balanced reading."""
    assert len(card_of(notify.build_payload(FakeHeadline()))["body"]) == 2


def test_headline_without_a_link_is_unaffected():
    headline = FakeHeadline()
    headline.link = None
    assert len(card_of(notify.build_payload(headline))["body"]) == 2


# --- transport ------------------------------------------------------------


def test_successful_post_sends_once(webhook, monkeypatch):
    stub = responder(202)
    monkeypatch.setattr(notify.httpx, "post", stub)
    notify.post({"text": "hi"})
    assert len(stub.calls) == 1


def test_transient_failures_are_retried(webhook, no_sleep, monkeypatch):
    stub = responder(503, 503, 202)
    monkeypatch.setattr(notify.httpx, "post", stub)
    notify.post({"text": "hi"})
    assert len(stub.calls) == 3


def test_gives_up_after_max_attempts(webhook, no_sleep, monkeypatch):
    stub = responder(503)
    monkeypatch.setattr(notify.httpx, "post", stub)
    with pytest.raises(notify.NotifyError, match="giving up"):
        notify.post({"text": "hi"})
    assert len(stub.calls) == notify._MAX_ATTEMPTS


def test_auth_failure_is_not_retried(webhook, no_sleep, monkeypatch):
    """401 means the trigger mode or credentials are wrong; retrying cannot fix it."""
    stub = responder(401)
    monkeypatch.setattr(notify.httpx, "post", stub)
    with pytest.raises(notify.NotifyError, match="401"):
        notify.post({"text": "hi"})
    assert len(stub.calls) == 1


def test_client_errors_are_not_retried(webhook, no_sleep, monkeypatch):
    stub = responder(400)
    monkeypatch.setattr(notify.httpx, "post", stub)
    with pytest.raises(notify.NotifyError):
        notify.post({"text": "hi"})
    assert len(stub.calls) == 1


def test_bearer_token_is_attached_when_configured(webhook, monkeypatch):
    monkeypatch.setattr(notify.settings, "teams_bearer_token", "tok123")
    stub = responder(202)
    monkeypatch.setattr(notify.httpx, "post", stub)
    notify.post({"text": "hi"})
    assert stub.calls[0]["headers"]["Authorization"] == "Bearer tok123"


def test_no_auth_header_in_anonymous_mode(webhook, monkeypatch):
    stub = responder(202)
    monkeypatch.setattr(notify.httpx, "post", stub)
    notify.post({"text": "hi"})
    assert "Authorization" not in stub.calls[0]["headers"]


def test_unconfigured_delivery_is_skipped_not_failed(monkeypatch):
    monkeypatch.setattr(notify.settings, "teams_enabled", False)
    result = notify.send_pending()
    assert result.skipped and result.sent == 0


# --- credential hygiene ---------------------------------------------------


def test_webhook_signature_is_redacted():
    """`sig` authenticates the call, so it must never reach a log or an API."""
    url = (
        "https://x.powerplatform.com/.../invoke"
        "?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=RP2iSL_Sm8NT4GLyn"
    )
    out = notify.redact(url)
    assert "RP2iSL_Sm8NT4GLyn" not in out
    assert "sig=<redacted>" in out
    assert "api-version=1" in out


def test_redacting_handles_missing_signature():
    assert notify.redact("") == ""
    assert notify.redact("https://x/invoke?api-version=1") == "https://x/invoke?api-version=1"

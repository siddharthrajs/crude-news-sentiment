"""Teams delivery: payload shape, retry behaviour and send-once guarantees."""

from datetime import datetime

import httpx
import pytest

from cns import notify


class FakeHeadline:
    id = 7
    title = "Iran threatens to close the Strait of Hormuz"
    category = "geo_risk"
    relevance_terms = "hormuz,iran,strait"
    link = "https://example.invalid/news/1"
    published_at = datetime(2026, 8, 26, 12, 0, 0)


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
    """Return a POST stub yielding the given statuses in order, recording calls."""
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        status = statuses[min(len(calls) - 1, len(statuses) - 1)]
        return httpx.Response(status, text="body", request=httpx.Request("POST", url))

    fake_post.calls = calls
    return fake_post


def test_payload_carries_text_and_structured_fields():
    payload = notify.build_payload(FakeHeadline())
    assert "Geopolitics" in payload["text"]
    assert FakeHeadline.title in payload["text"]
    assert payload["category"] == "geo_risk"
    assert payload["matched_terms"] == ["hormuz", "iran", "strait"]
    assert payload["url"] == FakeHeadline.link
    assert payload["headline_id"] == 7


def test_score_fields_exist_before_the_scorer_does():
    """The flow can be built against the final shape now."""
    payload = notify.build_payload(FakeHeadline())
    for key in ("score", "direction", "confidence"):
        assert key in payload
        assert payload[key] is None


def test_oil_and_geo_get_different_labels():
    geo = notify.build_payload(FakeHeadline())
    oil = FakeHeadline()
    oil.category = "oil_direct"
    assert "Geopolitics" in geo["text"]
    assert "Crude oil" in notify.build_payload(oil)["text"]


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

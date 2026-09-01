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


def test_unscored_card_is_just_the_headline():
    body = card_of(notify.build_payload(FakeHeadline()))["body"]
    assert len(body) == 1
    assert body[0]["text"] == FakeHeadline.title


def test_card_carries_no_sender_line():
    """The chat already shows who posted; a name in the card is redundant."""
    for text in texts(notify.build_payload(FakeHeadline())):
        assert not text.startswith("_")


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
    assert texts(payload) == [FakeHeadline.title, "BULLISH  +72"]


def test_bullish_and_bearish_are_coloured_differently():
    bull = notify.build_payload(FakeHeadline(), score=Score(72.0, "bullish", 0.8))
    bear = notify.build_payload(FakeHeadline(), score=Score(-60.0, "bearish", 0.7))
    assert card_of(bull)["body"][1]["color"] == "Good"
    assert card_of(bear)["body"][1]["color"] == "Attention"
    assert card_of(bear)["body"][1]["text"] == "BEARISH  -60"


def test_no_score_line_when_unscored():
    """A rendered zero would be indistinguishable from a balanced reading."""
    assert len(card_of(notify.build_payload(FakeHeadline()))["body"]) == 1


def test_headline_without_a_link_is_unaffected():
    headline = FakeHeadline()
    headline.link = None
    assert len(card_of(notify.build_payload(headline))["body"]) == 1


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


def test_send_pending_puts_the_score_on_the_card(monkeypatch):
    """The backlog sender used to post the headline with no direction.

    `build_payload` defaults `score` to None, and only the poller's inline send
    passed one -- so anything delivered from the backlog, which is everything
    after a restart, arrived in the chat as a bare title.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from cns import db as db_module
    from cns import notify, scoring
    from cns.models import Base, Headline, HeadlineScore

    # `send_pending` does `from .db import SessionLocal` at call time, so
    # patching the attribute on the module redirects it. Without this the test
    # runs against whatever DATABASE_URL points at, which is the live database.
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    monkeypatch.setattr(db_module, "SessionLocal", Local)

    posted = []
    monkeypatch.setattr(notify, "post", lambda payload: posted.append(payload))
    monkeypatch.setattr(notify.settings, "teams_enabled", True)
    monkeypatch.setattr(notify.settings, "teams_webhook_url", "https://example.invalid/x")

    with Local() as session:
        headline = Headline(
            source="test", external_id="score-on-card",
            title="Strait of Hormuz closed to all shipping",
            raw_title="Strait of Hormuz closed to all shipping",
            kind="narrative", category="geo_risk",
        )
        session.add(headline)
        session.flush()
        session.add(HeadlineScore(
            headline_id=headline.id, scorer_version=scoring.version(),
            category="geo_risk", score=72.0, confidence=0.8, label="bullish",
        ))
        session.commit()

    result = notify.send_pending(limit=50)
    assert result.sent == 1
    cards = [
        block["text"]
        for p in posted
        for block in p["attachments"][0]["content"]["body"]
    ]
    assert any("BULLISH" in t and "+72" in t for t in cards), cards


def test_send_pending_still_delivers_an_unscored_headline(monkeypatch):
    """The outer join must not hold back a headline that has no score row."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from cns import db as db_module
    from cns import notify
    from cns.models import Base, Headline

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    monkeypatch.setattr(db_module, "SessionLocal", Local)

    posted = []
    monkeypatch.setattr(notify, "post", lambda payload: posted.append(payload))
    monkeypatch.setattr(notify.settings, "teams_enabled", True)
    monkeypatch.setattr(notify.settings, "teams_webhook_url", "https://example.invalid/x")

    with Local() as session:
        session.add(Headline(
            source="test", external_id="no-score", title="Unscored headline",
            raw_title="Unscored headline", kind="narrative", category="geo_risk",
        ))
        session.commit()

    assert notify.send_pending(limit=50).sent == 1
    assert len(posted[0]["attachments"][0]["content"]["body"]) == 1

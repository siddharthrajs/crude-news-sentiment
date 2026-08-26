"""The pipeline runs fetch -> relevance -> score -> Teams -> database.

Delivery is inline with the poll. Nothing on a schedule sweeps the database to
send, so what is pinned here is the ordering and the failure behaviour.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from cns import notify, poller
from cns.models import Base, Headline
from cns.sources.financial_juice import FeedItem

OIL = "Saudi Aramco offers Arab medium crude oil for September loading"
GEO = "Iran threatens to close the Strait of Hormuz"
NOISE = "BOJ: governor Ueda will skip this week's Jackson Hole meeting"


def item(n, title):
    return FeedItem(
        external_id=str(n), title=title, raw_title=title,
        link=f"https://example.invalid/{n}", published_at=None,
    )


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    monkeypatch.setattr(poller, "SessionLocal", maker)
    with maker() as s:
        yield s


@pytest.fixture
def teams(monkeypatch):
    """Capture Teams posts and record whether the row existed at send time."""
    sent = []

    monkeypatch.setattr(notify, "is_configured", lambda: True)
    monkeypatch.setattr(poller.notify, "is_configured", lambda: True)

    def fake_post(payload):
        sent.append(payload)

    monkeypatch.setattr(poller.notify, "post", fake_post)
    return sent


def test_relevant_headlines_are_delivered_during_the_poll(session, teams):
    stored, filtered, delivered = poller._insert_new(
        session, [item(1, OIL), item(2, GEO)]
    )
    assert delivered == 2
    assert [p["headline"] for p in teams] == [OIL, GEO]


def test_irrelevant_headlines_are_stored_but_not_sent(session, teams):
    stored, filtered, delivered = poller._insert_new(session, [item(1, NOISE)])
    assert stored == 1 and delivered == 0
    assert teams == []
    assert session.scalar(select(Headline).where(Headline.external_id == "1")) is not None


def test_headline_id_is_available_to_the_payload(session, teams):
    """The row is flushed before delivery so the payload can carry its id."""
    poller._insert_new(session, [item(1, GEO)])
    assert teams[0]["headline_id"] is not None
    assert teams[0]["headline_id"] > 0


def test_delivery_is_recorded_on_the_saved_row(session, teams):
    poller._insert_new(session, [item(1, GEO)])
    row = session.scalar(select(Headline).where(Headline.external_id == "1"))
    assert row.notified_at is not None


def test_failed_delivery_still_saves_the_headline(session, monkeypatch):
    """A Teams outage must not cost us the headline."""
    monkeypatch.setattr(poller.notify, "is_configured", lambda: True)

    def boom(payload):
        raise notify.NotifyError("teams is down")

    monkeypatch.setattr(poller.notify, "post", boom)

    stored, _, delivered = poller._insert_new(session, [item(1, GEO)])
    assert stored == 1 and delivered == 0

    row = session.scalar(select(Headline).where(Headline.external_id == "1"))
    assert row is not None
    # Null marks it unsent, so the failure is visible and recoverable.
    assert row.notified_at is None


def test_one_failure_does_not_block_later_headlines(session, monkeypatch):
    monkeypatch.setattr(poller.notify, "is_configured", lambda: True)
    calls = []

    def flaky(payload):
        calls.append(payload)
        if len(calls) == 1:
            raise notify.NotifyError("transient")

    monkeypatch.setattr(poller.notify, "post", flaky)

    stored, _, delivered = poller._insert_new(session, [item(1, GEO), item(2, OIL)])
    assert stored == 2
    assert delivered == 1


def test_nothing_is_sent_when_teams_is_not_configured(session, monkeypatch):
    monkeypatch.setattr(poller.notify, "is_configured", lambda: False)
    stored, _, delivered = poller._insert_new(session, [item(1, GEO)])
    assert stored == 1 and delivered == 0


def test_already_seen_headlines_are_neither_stored_nor_resent(session, teams):
    poller._insert_new(session, [item(1, GEO)])
    stored, _, delivered = poller._insert_new(session, [item(1, GEO)])
    assert stored == 0 and delivered == 0
    assert len(teams) == 1


def test_scorer_result_reaches_the_payload(session, teams, monkeypatch):
    """The seam CrudeBERT will fill: score() output must land in the message."""
    from cns.scoring import Score

    monkeypatch.setattr(
        poller.scoring, "score",
        lambda h: Score(value=72.0, direction="bullish", confidence=0.8, event="supply_down"),
    )
    poller._insert_new(session, [item(1, GEO)])

    payload = teams[0]
    assert payload["score"] == 72.0
    assert payload["direction"] == "bullish"
    assert payload["confidence"] == 0.8
    assert payload["event"] == "supply_down"
    assert "BULLISH +72" in payload["text"]


def test_unscored_headlines_send_null_not_zero(session, teams):
    """Null means "not scored" and must stay distinct from a neutral 0."""
    poller._insert_new(session, [item(1, GEO)])
    assert teams[0]["score"] is None

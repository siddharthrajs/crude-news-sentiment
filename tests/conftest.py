from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cns.models import Base, Headline, HeadlineScore

NOW = datetime(2026, 8, 26, 12, 0, 0)
VERSION = "test-v1"


@pytest.fixture(autouse=True, scope="session")
def _never_touch_the_real_database():
    """Point `cns.db.SessionLocal` at throwaway sqlite for the whole test run.

    The `session` fixture below only helps tests that ask for it. Anything that
    exercises code calling `SessionLocal` directly -- `notify.send_pending`,
    `poller.poll_once` -- gets the engine built from DATABASE_URL, which in a
    working checkout is the live Postgres.

    That is not hypothetical. A test written against `send_pending` inserted a
    headline reading "Strait of Hormuz closed to all shipping" into production
    twice. It deleted the rows afterwards, which was not enough: `headlines` has
    an AFTER INSERT trigger that fires pg_notify, so both fake headlines were
    delivered to a live dashboard and no later DELETE could retract them.

    Autouse and session-scoped, so a test cannot reach the real database by
    forgetting to ask. Individual tests may still rebind it to their own engine.
    """
    from cns import db as db_module

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    original = db_module.SessionLocal
    db_module.SessionLocal = sessionmaker(bind=engine)
    try:
        yield
    finally:
        db_module.SessionLocal = original


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


@pytest.fixture
def add_scored(session):
    counter = {"n": 0}

    def _add(score, *, age_hours=1.0, confidence=1.0, novelty=1.0,
             salience=1.0, category="oil_direct"):
        counter["n"] += 1
        h = Headline(
            source="test",
            external_id=f"id-{counter['n']}",
            title=f"headline {counter['n']}",
            raw_title=f"headline {counter['n']}",
            published_at=NOW - timedelta(hours=age_hours),
        )
        session.add(h)
        session.flush()
        session.add(
            HeadlineScore(
                headline_id=h.id, scorer_version=VERSION, category=category,
                score=score, confidence=confidence, novelty=novelty, salience=salience,
            )
        )
        session.flush()
        return h

    return _add

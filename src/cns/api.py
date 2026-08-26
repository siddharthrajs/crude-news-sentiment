from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from sqlalchemy import func, select

from .config import settings
from . import market_index
from .db import SessionLocal, db_label, init_db
from .models import Headline, HeadlineScore, IndexSnapshot, PollRun
from .poller import poll_safe

log = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.add_job(
        poll_safe,
        "interval",
        seconds=settings.poll_interval_seconds,
        id="poll_feed",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    scheduler.add_job(
        snapshot_safe,
        "interval",
        minutes=settings.index_snapshot_interval_minutes,
        id="snapshot_index",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info("poller started (every %ss, db=%s)", settings.poll_interval_seconds, db_label())
    poll_safe()  # don't wait a full interval for the first sample
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


def snapshot_safe() -> None:
    """Record the index so it has a history to be z-scored against.

    Skips empty windows: snapshotting "no data" as 0.0 would drag the baseline
    toward neutral and make a genuinely quiet market look anomalous.
    """
    try:
        with SessionLocal() as session:
            for category in ("oil_direct", "geo_risk"):
                index = market_index.compute(
                    session,
                    scorer_version=settings.scorer_version,
                    category=category,
                    window_days=settings.index_window_days,
                    half_life_hours=settings.index_half_life_hours,
                )
                if index.volume:
                    market_index.snapshot(session, index)
    except Exception:
        log.exception("unhandled error during index snapshot")


app = FastAPI(title="crude-news-sentiment", lifespan=lifespan)


@app.get("/health")
def health():
    """Coolify healthcheck. Unhealthy if the last 3 polls all failed."""
    with SessionLocal() as session:
        recent = list(
            session.scalars(select(PollRun).order_by(PollRun.id.desc()).limit(3))
        )
    healthy = not recent or any(run.ok for run in recent)
    return {
        "status": "ok" if healthy else "degraded",
        "recent_polls": [
            {"at": r.started_at.isoformat(), "ok": bool(r.ok), "new": r.items_new}
            for r in recent
        ],
    }


@app.get("/stats")
def stats():
    with SessionLocal() as session:
        total = session.scalar(select(func.count(Headline.id))) or 0
        latest = session.scalar(select(func.max(Headline.published_at)))
        polls = session.scalar(select(func.count(PollRun.id))) or 0
        failed = session.scalar(
            select(func.count(PollRun.id)).where(PollRun.ok == 0)
        ) or 0
        by_kind = dict(
            session.execute(
                select(Headline.kind, func.count(Headline.id)).group_by(Headline.kind)
            ).all()
        )
    return {
        "headlines": total,
        "by_kind": by_kind,
        "narrative": by_kind.get("narrative", 0),
        "latest_published_at": latest.isoformat() if latest else None,
        "polls": polls,
        "polls_failed": failed,
        "poll_interval_seconds": settings.poll_interval_seconds,
    }


@app.get("/headlines")
def headlines(limit: int = 25, kind: str = "narrative"):
    """Narrative headlines by default.

    Pass `kind=calendar|widget|research` to inspect what the classifier is
    excluding, or `kind=all` for everything.
    """
    with SessionLocal() as session:
        query = select(Headline).order_by(Headline.published_at.desc())
        if kind != "all":
            query = query.where(Headline.kind == kind)
        rows = list(session.scalars(query.limit(min(limit, 200))))
    return [
        {
            "id": r.id,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "title": r.title,
            "kind": r.kind,
            "kind_rule": r.kind_rule,
            "link": r.link,
        }
        for r in rows
    ]


@app.get("/index")
def market_index_now(
    category: str = "oil_direct",
    window_days: int | None = None,
    half_life_hours: float | None = None,
):
    """Cumulative bull/bear index over the trailing window.

    Read `index_value` together with `volume`, `effective_n` and `dispersion`:
    a value near zero means "quiet" or "split" depending on those three.
    """
    with SessionLocal() as session:
        index = market_index.compute(
            session,
            scorer_version=settings.scorer_version,
            category=category,
            window_days=window_days or settings.index_window_days,
            half_life_hours=half_life_hours or settings.index_half_life_hours,
        )
    return index.as_dict()


@app.get("/index/history")
def market_index_history(category: str = "oil_direct", limit: int = 168):
    with SessionLocal() as session:
        rows = list(
            session.scalars(
                select(IndexSnapshot)
                .where(
                    IndexSnapshot.category == category,
                    IndexSnapshot.scorer_version == settings.scorer_version,
                )
                .order_by(IndexSnapshot.captured_at.desc())
                .limit(min(limit, 1000))
            )
        )
    return [
        {
            "captured_at": r.captured_at.isoformat(),
            "index_value": r.index_value,
            "volume": r.volume,
            "effective_n": r.effective_n,
            "dispersion": r.dispersion,
            "bull_share": r.bull_share,
            "bear_share": r.bear_share,
        }
        for r in reversed(rows)
    ]

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from sqlalchemy import func, select

from .config import settings
from .db import SessionLocal, db_label, init_db
from .models import Headline, PollRun
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
    scheduler.start()
    log.info("poller started (every %ss, db=%s)", settings.poll_interval_seconds, db_label())
    poll_safe()  # don't wait a full interval for the first sample
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


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
    return {
        "headlines": total,
        "latest_published_at": latest.isoformat() if latest else None,
        "polls": polls,
        "polls_failed": failed,
        "poll_interval_seconds": settings.poll_interval_seconds,
    }


@app.get("/headlines")
def headlines(limit: int = 25):
    with SessionLocal() as session:
        rows = list(
            session.scalars(
                select(Headline).order_by(Headline.published_at.desc()).limit(min(limit, 200))
            )
        )
    return [
        {
            "id": r.id,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "title": r.title,
            "link": r.link,
        }
        for r in rows
    ]

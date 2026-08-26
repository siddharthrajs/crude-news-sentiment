"""Poll the feed, store new headlines, record the run."""

from __future__ import annotations

import logging
import time

from sqlalchemy import select

from .classify import classify
from .db import SessionLocal
from .models import Headline, PollRun, utcnow
from .sources import financial_juice

log = logging.getLogger(__name__)


def _insert_new(session, items: list[financial_juice.FeedItem]) -> int:
    """Insert items we have not seen before, keyed on (source, external_id)."""
    if not items:
        return 0

    ids = [item.external_id for item in items]
    known = set(
        session.scalars(
            select(Headline.external_id).where(
                Headline.source == financial_juice.SOURCE_NAME,
                Headline.external_id.in_(ids),
            )
        )
    )

    fresh = [item for item in items if item.external_id not in known]
    # Oldest first, so `id` order matches publication order for later stages.
    for item in sorted(fresh, key=lambda i: (i.published_at or utcnow())):
        kind, rule = classify(item.title)
        session.add(
            Headline(
                source=financial_juice.SOURCE_NAME,
                external_id=item.external_id,
                title=item.title,
                raw_title=item.raw_title,
                link=item.link,
                published_at=item.published_at,
                kind=kind,
                kind_rule=rule,
            )
        )
    return len(fresh)


def poll_once() -> PollRun:
    started = time.monotonic()
    result = financial_juice.fetch()

    with SessionLocal() as session:
        new_count = _insert_new(session, result.items) if result.ok else 0
        run = PollRun(
            started_at=utcnow(),
            duration_ms=int((time.monotonic() - started) * 1000),
            status_code=result.status_code,
            items_seen=len(result.items),
            items_new=new_count,
            ok=1 if result.ok else 0,
            error=result.error,
        )
        session.add(run)
        session.commit()

    if result.ok:
        log.info(
            "poll ok: seen=%d new=%d in %dms", run.items_seen, run.items_new, run.duration_ms
        )
    else:
        log.error("poll failed: %s (status=%s)", result.error, result.status_code)
    return run


def poll_safe() -> None:
    """Scheduler entrypoint -- must never raise, or APScheduler drops the job."""
    try:
        poll_once()
    except Exception:
        log.exception("unhandled error during poll")


def reclassify_all() -> dict[str, int]:
    """Re-run the classifier over every stored headline.

    Safe to run repeatedly: classification is deterministic from the title, so
    this is how a rule change gets applied to the existing corpus.
    """
    counts: dict[str, int] = {}
    with SessionLocal() as session:
        for headline in session.scalars(select(Headline)):
            kind, rule = classify(headline.title)
            headline.kind, headline.kind_rule = kind, rule
            counts[kind] = counts.get(kind, 0) + 1
        session.commit()
    return counts

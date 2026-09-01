"""The pipeline: fetch -> relevance -> score -> Teams -> database.

Delivery happens inline, as each new headline is processed, rather than a
separate job sweeping the database for unsent rows.

The card is built from the headline itself and needs no database id, so
delivery happens before the row is written at all -- no flush, no staged state.

Each headline is committed on its own rather than batching the poll, so an
interruption mid-poll cannot leave delivered headlines unsaved. The residual
risk is one duplicate: if delivery succeeds and the commit then fails, the next
poll sees the headline as new. Duplicating a message is the better failure than
dropping one.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select

from . import notify, relevance, scoring
from .classify import NARRATIVE, classify
from .config import settings
from .db import SessionLocal
from .models import Headline, HeadlineScore, PollRun, utcnow
from .sources import financial_juice

log = logging.getLogger(__name__)


def _screen(title: str) -> tuple[str, str | None, str, str | None] | None:
    """Return the row fields for a headline worth storing, or None to discard.

    With STORE_IRRELEVANT on (the default) nothing is discarded: every headline
    is stored with its `kind` and `category` labels, and downstream stages
    select on those instead. Turning it off stores only narrative crude-oil and
    geopolitics headlines -- which is unrecoverable, since the feed exposes only
    a 100-item window.
    """
    kind, kind_rule = classify(title)
    if kind != NARRATIVE:
        category, terms = relevance.IRRELEVANT, []
    else:
        category, terms = relevance.classify(title)

    keep = kind == NARRATIVE and category != relevance.IRRELEVANT
    if not keep and not settings.store_irrelevant:
        return None
    return kind, kind_rule, category, ",".join(terms) or None


def _insert_new(session, items: list[financial_juice.FeedItem]) -> tuple[int, int, int]:
    """Insert items we have not seen before, keyed on (source, external_id).

    Returns ``(stored, filtered, delivered)``.
    """
    if not items:
        return 0, 0, 0

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
    stored = filtered = delivered = 0
    # Oldest first, so the chat reads in the order events happened and `id`
    # order matches publication order.
    for item in sorted(fresh, key=lambda i: (i.published_at or utcnow())):
        screened = _screen(item.title)
        if screened is None:
            filtered += 1
            continue
        kind, kind_rule, category, terms = screened

        headline = Headline(
            source=financial_juice.SOURCE_NAME,
            external_id=item.external_id,
            title=item.title,
            raw_title=item.raw_title,
            link=item.link,
            published_at=item.published_at,
            kind=kind,
            kind_rule=kind_rule,
            category=category,
            relevance_terms=terms,
        )
        # Teams first, then the database -- the card needs nothing from the row.
        result = _score(headline)
        if _deliver(headline, result):
            headline.notified_at = utcnow()
            delivered += 1

        session.add(headline)
        session.flush()
        if result is not None:
            session.add(
                HeadlineScore(
                    headline_id=headline.id,
                    scorer_version=scoring.version(),
                    category=headline.category,
                    score=result.value,
                    confidence=result.confidence,
                    salience=result.salience,
                    label=result.direction,
                    components=result.components,
                )
            )
        session.commit()
        stored += 1
    return stored, filtered, delivered


def _score(headline):
    """Score a relevant headline, or None. Never raises -- a scorer fault must
    not cost us the headline or the delivery."""
    if headline.kind != NARRATIVE or headline.category == relevance.IRRELEVANT:
        return None
    try:
        return scoring.score(headline)
    except Exception:
        log.exception("scoring failed for %r", headline.title[:60])
        return None


def _deliver(headline, result=None) -> bool:
    """Post a relevant headline to Teams. True if it was sent.

    Never raises: a delivery failure must not cost us the headline. The row is
    saved either way with `notified_at` left null, so a failed send is visible
    afterwards rather than silently lost.
    """
    if headline.kind != NARRATIVE or headline.category == relevance.IRRELEVANT:
        return False
    if not notify.is_configured():
        return False

    try:
        notify.post(notify.build_payload(headline, score=result))
    except notify.NotifyError as exc:
        log.error("Teams delivery failed for %r: %s", headline.title[:60], exc)
        return False
    return True


def poll_once() -> PollRun:
    started = time.monotonic()
    result = financial_juice.fetch()

    with SessionLocal() as session:
        if result.ok:
            new_count, filtered, delivered = _insert_new(session, result.items)
        else:
            new_count = filtered = delivered = 0
        run = PollRun(
            started_at=utcnow(),
            duration_ms=int((time.monotonic() - started) * 1000),
            status_code=result.status_code,
            items_seen=len(result.items),
            items_new=new_count,
            items_filtered=filtered,
            ok=1 if result.ok else 0,
            error=result.error,
        )
        session.add(run)
        session.commit()

    if result.ok:
        log.info(
            "poll ok: seen=%d stored=%d filtered=%d sent=%d in %dms",
            run.items_seen, run.items_new, run.items_filtered, delivered, run.duration_ms,
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
    """Re-run both filters over every stored headline.

    Safe to run repeatedly: both are deterministic from the title, so this is
    how a rule change gets applied to the existing corpus. It only relabels --
    removing rows that no longer pass is `purge_irrelevant`, kept separate
    because that deletion is irreversible.
    """
    counts: dict[str, int] = {}
    with SessionLocal() as session:
        for headline in session.scalars(select(Headline)):
            kind, rule = classify(headline.title)
            headline.kind, headline.kind_rule = kind, rule
            if kind == NARRATIVE:
                category, terms = relevance.classify(headline.title)
            else:
                category, terms = relevance.IRRELEVANT, []
            headline.category = category
            headline.relevance_terms = ",".join(terms) or None
            key = category if kind == NARRATIVE else kind
            counts[key] = counts.get(key, 0) + 1
        session.commit()
    return counts


def purge_irrelevant(dry_run: bool = True) -> int:
    """Delete stored headlines that the current filters reject.

    Defaults to a dry run: the feed's 100-item window means a deleted headline
    is gone for good, so the count is worth reading before committing to it.
    """
    with SessionLocal() as session:
        doomed = list(
            session.scalars(
                select(Headline).where(
                    (Headline.kind != NARRATIVE)
                    | (Headline.category == relevance.IRRELEVANT)
                )
            )
        )
        if not dry_run:
            for headline in doomed:
                session.delete(headline)
            session.commit()
    return len(doomed)

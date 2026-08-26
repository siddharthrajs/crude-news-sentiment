"""Database schema.

All datetimes are stored as naive UTC so the schema behaves identically on
SQLite (local dev) and Postgres (Coolify).
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Headline(Base):
    """One raw headline as published by the source.

    Nothing is filtered at ingest -- rejects are the negative examples we need
    later to tune the relevance filter, so everything the feed emits lands here.
    """

    __tablename__ = "headlines"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_headline_source_extid"),
        Index("ix_headlines_published_at", "published_at"),
        Index("ix_headlines_kind", "kind"),
        Index("ix_headlines_category", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    raw_title: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str | None] = mapped_column(Text)

    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    #: narrative | calendar | widget | research -- see cns.classify.
    #: Only `narrative` items continue down the pipeline. Non-narrative rows are
    #: retained rather than dropped: re-ingesting is capped by the feed's
    #: 100-item window, so a discarded row is gone for good.
    kind: Mapped[str] = mapped_column(String(16), default="narrative", nullable=False)
    #: Which rule matched, so a classification can be audited after the fact.
    kind_rule: Mapped[str | None] = mapped_column(String(48))

    #: oil_direct | geo_risk -- see cns.relevance. Irrelevant headlines are not
    #: stored at all (unless STORE_IRRELEVANT is on), so this is in practice
    #: always one of the two relevant values.
    category: Mapped[str] = mapped_column(String(16), default="oil_direct", nullable=False)
    #: Lexicon terms that triggered the match, so a decision can be audited.
    relevance_terms: Mapped[str | None] = mapped_column(String(200))


class PollRun(Base):
    """One poll attempt. Gives us an audit trail for feed reliability."""

    __tablename__ = "poll_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status_code: Mapped[int | None] = mapped_column(Integer)
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    #: New items discarded by the kind/relevance filters. Counted even though
    #: the text is not kept, so filter behaviour stays observable over time.
    items_filtered: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class HeadlineScore(Base):
    """A score produced by one version of the scorer for one headline.

    Keyed on (headline_id, scorer_version) rather than living on `headlines`,
    because the scoring model will be retuned repeatedly. Storing scores as a
    column would destroy the old values on every retune and make it impossible
    to compare a new scorer against the old one on identical input. Here,
    rescoring the whole corpus is an insert, and two versions can be diffed.
    """

    __tablename__ = "headline_scores"
    __table_args__ = (
        UniqueConstraint("headline_id", "scorer_version", name="uq_score_headline_version"),
        Index("ix_scores_version_category", "scorer_version", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    headline_id: Mapped[int] = mapped_column(
        ForeignKey("headlines.id", ondelete="CASCADE"), nullable=False
    )
    scorer_version: Mapped[str] = mapped_column(String(64), nullable=False)

    # oil_direct | geo_risk | calendar | irrelevant
    category: Mapped[str] = mapped_column(String(32), nullable=False)

    #: Bearish -100 .. +100 bullish.
    score: Mapped[float] = mapped_column(Float, nullable=False)

    #: Aggregation weights, all 0..1, supplied by the scorer.
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    #: Downweights a story the feed has already told us -- see index docstring.
    novelty: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    #: How much this event moves crude at all (OPEC decision >> minor producer).
    salience: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    label: Mapped[str | None] = mapped_column(String(32))
    #: Per-component breakdown, so a score is auditable after the fact.
    components: Mapped[dict | None] = mapped_column(JSON)
    scored_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class IndexSnapshot(Base):
    """The market index as computed at one point in time.

    The index is cheap to recompute from `headline_scores`, so this is not a
    cache. It exists because the index's own history is the thing you want to
    chart and to z-score against -- and that history cannot be reconstructed
    later, since a rescore would change what past values "were".
    """

    __tablename__ = "index_snapshots"
    __table_args__ = (
        Index("ix_snapshots_captured", "captured_at"),
        Index("ix_snapshots_version_category", "scorer_version", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    scorer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    half_life_hours: Mapped[float] = mapped_column(Float, nullable=False)

    index_value: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_n: Mapped[float] = mapped_column(Float, nullable=False)
    dispersion: Mapped[float] = mapped_column(Float, nullable=False)
    bull_share: Mapped[float] = mapped_column(Float, nullable=False)
    bear_share: Mapped[float] = mapped_column(Float, nullable=False)

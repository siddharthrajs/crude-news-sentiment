"""Database schema.

All datetimes are stored as naive UTC so the schema behaves identically on
SQLite (local dev) and Postgres (Coolify).
"""

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
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
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    raw_title: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str | None] = mapped_column(Text)

    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class PollRun(Base):
    """One poll attempt. Gives us an audit trail for feed reliability."""

    __tablename__ = "poll_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status_code: Mapped[int | None] = mapped_column(Integer)
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

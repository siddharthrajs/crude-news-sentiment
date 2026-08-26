import os
from urllib.parse import urlparse

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base


def _ensure_sqlite_dir(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    path = url.split("///", 1)[-1]
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


#: Columns added after the initial release, as (table, column, DDL type + default).
#: The schema only ever grows, so an inspect-and-add pass is enough and avoids
#: pulling in Alembic. Revisit if a change ever needs to rewrite existing data.
_ADDED_COLUMNS = (
    ("headlines", "kind", "VARCHAR(16) NOT NULL DEFAULT 'narrative'"),
    ("headlines", "kind_rule", "VARCHAR(48)"),
)


def _apply_additive_migrations() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _apply_additive_migrations()


def db_label() -> str:
    parsed = urlparse(settings.database_url)
    return parsed.scheme.split("+")[0]

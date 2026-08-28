import logging
import os
from urllib.parse import urlparse

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base

log = logging.getLogger(__name__)


def normalize_url(url: str) -> str:
    """Accept the `postgres://` form that hosting providers hand out.

    SQLAlchemy needs an explicit driver, and rejects the bare `postgres://`
    scheme outright.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _ensure_sqlite_dir(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    path = url.split("///", 1)[-1]
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)


DATABASE_URL = normalize_url(settings.database_url)
IS_POSTGRES = DATABASE_URL.startswith("postgresql")
#: SQLite has no schemas, so this only applies to Postgres.
SCHEMA = settings.db_schema if (IS_POSTGRES and settings.db_schema) else None

_ensure_sqlite_dir(DATABASE_URL)

_connect_args: dict = {}
if SCHEMA:
    # Unqualified names resolve to our schema; `public` stays readable so a
    # backtest can join headline scores against price tables living there.
    _connect_args["options"] = f"-csearch_path={SCHEMA},public"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    future=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


#: Columns added after the initial release, as (table, column, DDL type + default).
#: The schema only ever grows, so an inspect-and-add pass is enough and avoids
#: pulling in Alembic. Revisit if a change ever needs to rewrite existing data.
_ADDED_COLUMNS = (
    ("headlines", "kind", "VARCHAR(16) NOT NULL DEFAULT 'narrative'"),
    ("headlines", "kind_rule", "VARCHAR(48)"),
    ("headlines", "category", "VARCHAR(16) NOT NULL DEFAULT 'oil_direct'"),
    ("headlines", "relevance_terms", "VARCHAR(200)"),
    ("poll_runs", "items_filtered", "INTEGER NOT NULL DEFAULT 0"),
    ("headlines", "zs_category", "VARCHAR(16)"),
    ("headlines", "zs_score", "DOUBLE PRECISION" if IS_POSTGRES else "FLOAT"),
    ("headlines", "zs_scored_at", "TIMESTAMP"),
    ("headlines", "notified_at", "TIMESTAMP"),
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


#: Channel a downstream consumer holds `LISTEN` on to be woken the moment a
#: headline commits, instead of polling this database on a timer.
NOTIFY_CHANNEL = "cns_headline"


def _apply_notify_trigger() -> None:
    """Install the trigger that pushes new headlines to `LISTEN` consumers.

    The payload is the row id alone. A headline's score is inserted later in
    the same transaction, and NOTIFY is delivered at COMMIT, so a listener that
    re-queries on wake always sees the two together -- whereas a payload built
    at insert time would carry the headline without its score. It also keeps us
    clear of pg_notify's 8000-byte payload limit.

    Postgres only, and best-effort: the pipeline itself does not consume this,
    so a database user without rights to create functions should still get a
    working pipeline rather than a refusal to start.
    """
    if not IS_POSTGRES:
        return
    schema = SCHEMA or "public"
    try:
        with engine.begin() as conn:
            conn.execute(text(f'''
                CREATE OR REPLACE FUNCTION "{schema}".notify_headline()
                RETURNS trigger AS $$
                BEGIN
                  PERFORM pg_notify('{NOTIFY_CHANNEL}', NEW.id::text);
                  RETURN NULL;
                END;
                $$ LANGUAGE plpgsql
            '''))
            # Created only when absent rather than dropped and recreated: DROP
            # TRIGGER takes an ACCESS EXCLUSIVE lock on `headlines`, and this
            # runs on every startup.
            exists = conn.execute(
                text(
                    "SELECT 1 FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :schema AND c.relname = 'headlines' "
                    "AND t.tgname = 'headlines_notify'"
                ),
                {"schema": schema},
            ).first()
            if not exists:
                # The WHEN clause filters at the source, so consumers are not
                # woken for calendar entries, widgets or irrelevant headlines.
                conn.execute(text(f'''
                    CREATE TRIGGER headlines_notify
                    AFTER INSERT ON "{schema}".headlines
                    FOR EACH ROW
                    WHEN (NEW.kind = 'narrative' AND NEW.category <> 'irrelevant')
                    EXECUTE FUNCTION "{schema}".notify_headline()
                '''))
    except Exception:
        log.warning(
            "could not install the %s notify trigger; the pipeline is "
            "unaffected, but consumers listening for live headlines will not "
            "be woken", NOTIFY_CHANNEL, exc_info=True,
        )


def init_db() -> None:
    if SCHEMA:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
    Base.metadata.create_all(engine)
    _apply_additive_migrations()
    _apply_notify_trigger()


def db_label() -> str:
    scheme = urlparse(DATABASE_URL).scheme.split("+")[0]
    return f"{scheme}:{SCHEMA}" if SCHEMA else scheme

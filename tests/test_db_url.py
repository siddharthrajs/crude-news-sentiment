from cns.db import normalize_url


def test_provider_style_postgres_scheme_is_given_a_driver():
    assert normalize_url("postgres://u:p@h:5433/db") == "postgresql+psycopg://u:p@h:5433/db"
    assert normalize_url("postgresql://u:p@h:5433/db") == "postgresql+psycopg://u:p@h:5433/db"


def test_explicit_driver_and_sqlite_are_left_alone():
    assert normalize_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_url("sqlite:///./data/cns.db") == "sqlite:///./data/cns.db"


def test_password_containing_special_chars_survives():
    url = "postgres://postgres:aB1/2+3=@10.0.0.1:5433/postgres"
    assert normalize_url(url).endswith("@10.0.0.1:5433/postgres")

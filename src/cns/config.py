from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/cns.db"

    feed_url: str = "https://www.financialjuice.com/feed.ashx?xy=rss"
    poll_interval_seconds: int = 90
    http_timeout_seconds: float = 25.0
    user_agent: str = "crude-news-sentiment/0.1"

    #: Bump whenever scoring logic changes -- old scores are kept, not overwritten.
    scorer_version: str = "v0"
    index_window_days: int = 7
    index_half_life_hours: float = 24.0
    index_snapshot_interval_minutes: int = 60

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"


settings = Settings()

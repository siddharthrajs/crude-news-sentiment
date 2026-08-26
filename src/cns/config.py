from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/cns.db"
    #: Postgres schema to own our tables. Keeps them out of `public`, which on a
    #: shared instance may already hold unrelated data. Ignored for SQLite.
    db_schema: str = "cns"

    feed_url: str = "https://www.financialjuice.com/feed.ashx?xy=rss"
    poll_interval_seconds: int = 90
    http_timeout_seconds: float = 25.0
    user_agent: str = "crude-news-sentiment/0.1"

    #: Keep headlines the filters reject, labelled but excluded from scoring.
    #: On by default: the feed only exposes a 100-item window, so a discarded
    #: headline is unrecoverable, and the rejects are the negative examples the
    #: relevance lexicon has to be tuned against. Filtering happens at query
    #: time on `kind` and `category`, not by throwing rows away.
    store_irrelevant: bool = True

    #: Zero-shot relevance as a second opinion. Needs the `ml` extra installed.
    zeroshot_enabled: bool = False
    zeroshot_model: str = "facebook/bart-large-mnli"
    #: Minimum combined probability of the two relevant categories for a
    #: headline to count as relevant -- oil+geo mass, not the top-1 score.
    #: Swept against the labelled set by scripts/tune_zeroshot.py: 0.80 gives
    #: recall 0.89 / precision 0.96. Note the softmax has two relevant labels
    #: against one, so combined mass sits near 0.67 at chance -- anything below
    #: ~0.7 keeps almost everything.
    zeroshot_threshold: float = 0.80
    #: Headlines per pass. Kept small so a backlog never blocks ingestion.
    zeroshot_batch_size: int = 16
    zeroshot_interval_minutes: int = 5

    #: Microsoft Teams delivery via a Power Automate flow. Incoming webhooks
    #: are not usable: the connector was retired end of 2025 and was
    #: channel-only regardless, never group chats.
    teams_enabled: bool = False
    teams_webhook_url: str = ""
    teams_interval_minutes: int = 2
    #: Cap per run so a backlog cannot dump the whole corpus into the chat.
    teams_max_per_run: int = 10

    #: Only needed if the flow's trigger requires Entra OAuth rather than
    #: being callable by anyone with the URL.
    teams_tenant_id: str = ""
    teams_client_id: str = ""
    teams_client_secret: str = ""
    teams_scope: str = "https://service.flow.microsoft.com/.default"
    #: Escape hatch: paste a token directly instead of client credentials.
    teams_bearer_token: str = ""

    #: sentiment = FinBERT net sentiment of the wording (simple, but inherits
    #: the tone-vs-price inversion). event = supply/demand rules set direction.
    #: Scores are stored under separate versions, so switching adds a second
    #: opinion rather than overwriting the first.
    scorer_mode: str = "sentiment"

    #: FinBERT supplies wording intensity only -- never direction. Needs the ml
    #: extra; without it the scorer runs on rules alone at lower confidence.
    finbert_enabled: bool = True
    finbert_model: str = "ProsusAI/finbert"

    #: Bump whenever scoring logic changes -- old scores are kept, not overwritten.
    scorer_version: str = "v0"
    index_window_days: int = 7
    index_half_life_hours: float = 24.0
    index_snapshot_interval_minutes: int = 60

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"


settings = Settings()

# crude-news-sentiment

Real-time sentiment pipeline for crude oil and geopolitics headlines from
[FinancialJuice](https://www.financialjuice.com/), delivered to Microsoft Teams.

**Status: stage 1 done, stage 5 scaffolded.** The poller captures headlines and the
market index engine is built and tested. Filtering, scoring and Teams delivery are
not built yet, so `/index` returns `no data` until scores exist.

## Pipeline

| Stage | What it does | Status |
|---|---|---|
| 1. Ingest | Poll FinancialJuice RSS, dedupe, persist | ✅ done |
| 2. Filter | Split `oil_direct` / `geo_risk` from noise | todo |
| 3. Score | Supply/demand event model → bullish/bearish | todo |
| 4. Notify | Adaptive Card → Teams group chat | todo |
| 5. Index | Cumulative 7-day bull/bear measure | ✅ engine done, needs stage 3 |
| 6. Backtest | Index vs WTI/Brent moves | todo |

## Feed behaviour

Measured against the live feed, not assumed:

- **Endpoint:** `https://www.financialjuice.com/feed.ashx?xy=rss` (`text/xml; charset=utf-8`)
- **Window:** 100 items, ~15 hours of history at a typical rate of ~0.11 headlines/min
- **Rate limit:** Cloudflare allows ~1 request/min per IP. A second request inside
  that window returns `429` + `Retry-After: ~41-60` and the body `error code: 1015`.

The 100-item window is the safety net: at 90s polling we only need ~0.2 new items
per tick, so the poller can be down for hours and still backfill without gaps.
During heavy news the rate spikes and the window shrinks, so downtime tolerance
falls — `/stats` tracks failed polls so gaps are visible.

`fetch()` retries **once** on a 429 if `Retry-After` is under 60s, since losing a
poll costs a full interval of headlines.

## Design notes

- **Nothing is filtered at ingest.** Rejected headlines are the negative examples
  needed to tune the relevance filter in stage 2, so everything the feed emits is stored.
- **Dedupe** is on `(source, external_id)` where `external_id` is the RSS `guid`.
- **Datetimes** are stored as naive UTC so SQLite and Postgres behave identically.
- The `FinancialJuice: ` title prefix is stripped into `title`; `raw_title` keeps the original.
- Roughly half the feed is econ-calendar prints
  (`US MBA 30-Yr Mortgage Rate Actual 6.78% (Forecast -, Previous 6.77%)`).
  These are structured, not narrative — stage 2 must route them separately, and for
  EIA/API inventories the *surprise vs forecast* is the signal, not the wording.

## The market index

`GET /index` returns a cumulative bull/bear measure over a trailing window
(7 days by default) of scored headlines. Three deliberate choices:

**Weighted mean, not a sum.** A sum tracks *news volume* rather than sentiment —
a quiet bullish week would score below a noisy, evenly-split one. Dividing by
total weight keeps the index in `[-100, +100]` and comparable week to week.

**Exponential time decay**, halving every `INDEX_HALF_LIFE_HOURS` (default 24),
so a six-day-old headline does not count like an hour-old one.

**Never read `index_value` alone.** A value near zero has two completely
different meanings, and the accompanying fields are what separate them:

| Field | Why it matters |
|---|---|
| `volume` | Headlines in the window |
| `effective_n` | Kish effective sample size — collapses toward 1 when one headline dominates the weights |
| `dispersion` | Weighted spread. High + index≈0 means a **split** market, not a quiet one |
| `bull_share` / `bear_share` | Weight split by direction |
| `zscore` | Position against the index's own history; `null` until 30 snapshots exist |

Per-headline weights are `time_decay × confidence × novelty × salience`.
`confidence`, `novelty` and `salience` come from the scorer — story-clustering
belongs to stage 3, and this module only consumes its output.

### Why scores are a separate table

`headline_scores` is keyed on `(headline_id, scorer_version)` rather than being
columns on `headlines`. The scoring model will be retuned repeatedly; storing
scores inline would destroy the previous values on every retune and make it
impossible to compare a new scorer against the old one on identical input.
Rescoring the corpus is an insert, and two versions can be diffed directly.

`index_snapshots` is not a cache — the index is cheap to recompute. It exists
because the index's *own* history is what you chart and z-score against, and
that history cannot be rebuilt later, since a rescore changes what past values
would have been. Empty windows are not snapshotted: recording `0.0` for
"no data" would drag the baseline toward neutral.

## Local development

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e .   # Linux/macOS: .venv/bin/python
cp .env.example .env
.venv/Scripts/python -m cns
```

Defaults to SQLite at `./data/cns.db`. Then:

```bash
curl localhost:8000/health          # poll health, for Coolify
curl localhost:8000/stats           # counts, latest headline, failed polls
curl localhost:8000/headlines?limit=25
```

Tests: `.venv/Scripts/python -m pytest tests -q`

## Deploying to Coolify

Create a **Docker Compose** resource pointing at this repo. Set environment variables:

| Variable | Notes |
|---|---|
| `POSTGRES_PASSWORD` | required |
| `POLL_INTERVAL_SECONDS` | defaults to `90` |
| `LOG_LEVEL` | defaults to `INFO` |

To use an existing Postgres instead of the bundled one, set `DATABASE_URL` to
`postgresql+psycopg://user:pass@host:5432/dbname` and drop the `db` service.
Otherwise `DATABASE_URL` is wired to the `db` service by compose. Point Coolify's healthcheck
at `/health`; it reports `degraded` only when the last three polls all failed, so a
single transient 429 will not restart the container.

**Run exactly one instance.** The rate limit is per IP and the poller assumes it is
the only writer — two replicas would 429 each other continuously.

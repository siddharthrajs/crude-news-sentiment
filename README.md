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
| 2a. Classify | Drop non-narrative feed noise | ✅ done |
| 2b. Filter | Keep only `oil_direct` / `geo_risk` | ✅ done |
| 3. Score | Event rules + FinBERT intensity | ✅ done |
| 4. Notify | Power Automate → Teams group chat | ✅ live |
| 5. Index | Cumulative 7-day bull/bear measure | ✅ engine done, needs stage 3 |
| 6. Backtest | Index vs WTI/Brent moves | todo |

## Feed behaviour

Measured against the live feed, not assumed:

- **Endpoint:** `https://www.financialjuice.com/feed.ashx?xy=rss` (`text/xml; charset=utf-8`)
- **Window:** 100 items, ~15 hours of history at a typical rate of ~0.11 headlines/min
- **Rate limit:** Cloudflare allows ~1 request/min per IP. A second request inside
  that window returns `429` + `Retry-After: 41-60` and the body `error code: 1015`.
  `POLL_INTERVAL_SECONDS` is 61 — just above the measured floor. Do not go lower.

The 100-item window is the safety net: at 90s polling we only need ~0.2 new items
per tick, so the poller can be down for hours and still backfill without gaps.
During heavy news the rate spikes and the window shrinks, so downtime tolerance
falls — `/stats` tracks failed polls so gaps are visible.

`fetch()` retries once on a 429 **only when the wait finishes well before the
next scheduled poll**. Retrying blocks the job for the whole wait, which was
worth it at a 90s interval but is harmful at 61s: a 60s sleep outlives the next
tick, which APScheduler then skips (`max_instances=1`), turning a 61s cadence
into ~122s. At 61s a 429 is simply skipped and the next poll picks it up.

## Design notes

- **Nothing is filtered at ingest.** Rejected headlines are the negative examples
  needed to tune the relevance filter in stage 2, so everything the feed emits is stored.
- **Dedupe** is on `(source, external_id)` where `external_id` is the RSS `guid`.
- **Datetimes** are stored as naive UTC so SQLite and Postgres behave identically.
- The `FinancialJuice: ` title prefix is stripped into `title`; `raw_title` keeps the original.
## What gets filtered out

Measured over 100 captured items, **37% of the feed is not a headline at all**.
`cns.classify` splits it into three kinds, all derived from inspecting real
captured titles:

Current corpus, by `kind` x `category`:

```
narrative  irrelevant   54     calendar   irrelevant   30
narrative  geo_risk     27     widget     irrelevant   17
narrative  oil_direct    5     research   irrelevant    9
```

| Kind | Share | Example |
|---|---|---|
| `narrative` | 63% | `Saudi Aramco offers Arab medium, heavy crude oil for September loading` |
| `widget` | 17% | `90-Day Correlation Matrix`, `FX Implied Volatility`, `BoJ Interest Rate Probabilities` |
| `calendar` | 16% | `Swedish PPI YoY Actual 6.4% (Forecast -, Previous 7.4%)` |
| `research` | 4% | `MUFG: The AUD - FJElite` |

Only `narrative` continues down the pipeline. `/headlines` returns narrative by
default; pass `?kind=calendar|widget|research|all` to audit what is excluded.

Non-narrative rows are **marked, not deleted**. Re-ingesting is capped by the
feed's 100-item window, so a dropped row is unrecoverable. `kind` is stored as a
column (unlike scores) because classification is deterministic from the title:
`reclassify_all()` re-runs the rules over the corpus idempotently after a rule
change, losing nothing.

> **Known trade-off:** the `calendar` bucket includes the weekly
> `US API Crude Oil Stock Change` / `Cushing` / `Distillate` / `Gasoline` prints,
> which are among the strongest scheduled drivers of crude. They are excluded by
> choice. The rows are retained, so routing them back in is a filter change plus
> a surprise-vs-forecast parser — not a re-ingest.

## Relevance filter

Every headline is **stored and labelled**; the filter decides what moves on to
scoring, not what survives. Downstream stages select on `kind` and `category`
rather than relying on rows being absent.

`oil_direct` is a lexicon hit on the oil complex (crude, Brent, OPEC, bbl,
refinery, tanker, Cushing, ...). It deliberately excludes the bare word
*energy*: "civil nuclear energy with Saudi Arabia" is geopolitics, not an oil story.

`geo_risk` is harder, because naming a producer is not enough — *"Canada's PM
Carney set to address the EU Parliament"* names a top-five producer and means
nothing for crude. So actors are tiered:

| Tier | Behaviour | Members |
|---|---|---|
| Chokepoints | always pass | Hormuz, Bab el-Mandeb, Suez, Red Sea, Malacca, "Middle East" |
| Tier 1 actors | pass alone | Iran, OPEC, Saudi/Aramco, Russia, Venezuela, Libya, Houthis |
| Standalone risk | passes alone | sanctions, embargo, oil price cap |
| Tier 2 actors | need a risk term | Ukraine, Israel, Iraq, Qatar, UAE, Nigeria, Kazakhstan, ... |

Risk terms are war, strike, drone, missile, blockade, seizure, ceasefire,
nuclear, escalation and similar. Oil wins over geo when both match, being the
more direct signal. Matched terms are stored in `relevance_terms` so any
decision can be audited.

### Accuracy

Measured by `python scripts/eval_relevance.py` against `tests/labels.py`, a
checked-in set of 79 hand-read headlines:

```
labelled: 79   correct: 79   accuracy: 100.0%
false positives (noise kept):      0
false negatives (DROPPED FOREVER): 0
```

**This is in-sample.** The lexicon was tuned on those same 79 headlines, so
accuracy will fall on unseen data. The number is a regression guard, not a
performance claim. `tests/test_relevance.py` ratchets it so a lexicon change
cannot silently make things worse; the binding assertion is that false
negatives stay at zero.

Rejects are retained deliberately (`STORE_IRRELEVANT`, on by default). They are
the negative examples the lexicon has to be tuned against, and the feed's
100-item window makes a discarded headline unrecoverable — there would be no way
to discover one the lexicon wrongly dropped. Setting it to `false` stores only
`oil_direct` and `geo_risk`, at that cost.

## Zero-shot second opinion (optional)

A `facebook/bart-large-mnli` classifier runs alongside the lexicon and records a
second verdict in `zs_category` / `zs_score`. It is **advisory** — it never
overwrites `category`. The point is `GET /disagreements`: where the two differ
is either a lexicon blind spot or a model mistake, and reading those is how the
labelled set grows past what the lexicon already knows.

Enable with `ZEROSHOT_ENABLED=true` and the `ml` extra installed
(`pip install -e ".[ml]"`, or `--build-arg INSTALL_ML=1`). Costs ~2GB RAM and
~15s cold start; scoring runs on its own 5-minute job so ingestion never waits.

### Picking the decision rule

The obvious rule — take the highest-scoring label — is wrong here. The model
splits probability across the two relevant labels, so *"Trump says nuclear deal
with Saudi Arabia will advance"* came back geo 0.48 / oil 0.44: no winner,
despite 0.92 of the mass saying it is not noise. The rule is therefore
**combined oil+geo mass against a threshold**, then the larger side names the
category.

Note the softmax has two relevant labels against one, so combined mass sits near
0.67 at chance — a threshold below ~0.7 keeps nearly everything. Swept over the
labelled set with `scripts/tune_zeroshot.py`:

| threshold | recall | precision | accuracy |
|---|---|---|---|
| 0.50 | 1.00 | 0.49 | 0.63 |
| 0.70 | 0.93 | 0.81 | 0.90 |
| **0.80** | **0.89** | **0.96** | **0.95** |
| 0.90 | 0.79 | 1.00 | 0.92 |

Scoring each label independently (`multi_label=True`) was tried and is worse —
recall caps at 0.82.

### Does it actually help?

Not yet, on the data so far. Across 86 narrative headlines the two agree 84% of
the time, and every disagreement where zero-shot claimed relevance the lexicon
had missed turned out to be a **model false positive** (two Meta lawsuit
headlines, one about shipping costs). It found no real lexicon blind spots.

Standalone it scores 86% against the labelled set versus the lexicon's 100% —
though the lexicon was tuned on that set, so its number is inflated. Taking
either verdict as relevant gives 98.7%.

The case for keeping it is prospective: it reads meaning rather than words, so
it should catch phrasings the lexicon has no term for. That has not yet been
demonstrated on real data, which is why it is off by default.

## Scoring

Two scorers, chosen by `SCORER_MODE`. Scores are stored under a version that
**includes the mode** (`v0-sentiment`, `v0-event`), so switching adds a second
opinion rather than overwriting the first and the two stay comparable on
identical headlines.

### `sentiment` (default)

FinBERT net sentiment, `P(positive) - P(negative)` scaled to +/-100, with the
sign flipped for headlines that are **not** direct market reports. Confidence is
the non-neutral mass. Scores every headline - 44/44 on the live corpus.

The flip exists because FinBERT judges tone, and for crude the tone usually runs
backwards: supply cuts, outages, sanctions, blocked tankers and war all sound
negative and are bullish.

But it is exactly wrong where tone and price already agree - a headline
reporting oil's own numbers moving. So the flip is **routed**, not blanket:
`events.describes_market_directly()` detects price, demand and inventory
reports, and those keep their original sign.

Measured over 18 headlines with unambiguous direction
(`python scripts/eval_inversion.py`):

| | plain | blanket flip | **routed** |
|---|---|---|---|
| Overall | 6/18 (33%) | 11/18 (61%) | **17/18 (94%)** |
| Supply / risk | 0/12 | 11/12 | **11/12** |
| Demand / price | 6/6 | 0/6 | **6/6** |

Three details the routing depends on:

- A percentage does not make a supply decision a price report - "OPEC raises
  production quotas by 5%" is a supply story, and inverting it is right.
- In "below $60 a barrel" the barrel is a price unit, not a supply noun.
- Price-move verbs are a separate vocabulary from supply verbs, because "eases"
  shrinks a price but *grows* supply in "eases sanctions".

The one remaining miss is `Iran and Oman agree ceasefire terms`, which FinBERT
reads as near-neutral (+/-8) and lands inside the neutral band. That is a model
limit, not a routing one.

`SCORER_INVERT=false` gives plain sentiment; `SCORER_MODE=event` takes direction
from the event and is not exposed to any of this.

### `event`

Direction from the supply/demand event in `cns.events`; FinBERT contributes only
intensity, never direction.

| Event | Price effect |
|---|---|
| supply down (cut, outage, sanctions, inventory draw) | bullish |
| supply up (quota rise, glut, inventory build) | bearish |
| demand up / down | bullish / bearish |
| risk up / down | bullish / bearish |

Inventories are handled explicitly because they read backwards: a *build* is oil
sitting unused and is bearish, however positive "rising" sounds.

Immune to the tone inversion, but blind to phrasings its vocabulary misses, and
it **abstains on ~60%** of relevant headlines (17/43). It has no negation
handling either — `An agreement ... has not yet been finalised` matches
"agreement" and scores it as de-escalation.

Unreadable headlines score `None`, never `0.0`: "could not be read" and "read as
balanced" must stay distinguishable or the index averages in placeholders.

### Why not CrudeBERT

`Captain-1337/CrudeBERT` was the intended scorer and does not work. On our
corpus it predicts the **same class for all 30 headlines**, per-class standard
deviation `[0.006, 0.064, 0.064]`. Opposite-direction controls return identical
probabilities:

```
"OPEC announces deep cuts to crude production quotas"    [0.062, 0.502, 0.435]
"OPEC raises production quotas sharply for next quarter" [0.062, 0.502, 0.435]
```

Weights load without a newly-initialized warning, so this is the published
checkpoint. Its config is self-contradictory too — `id2label` keyed `-1/0/1`,
disagreeing with `label2id`. `scripts/eval_crudebert.py` reproduces it.

## Teams delivery

Incoming webhooks are not usable here. The Office 365 connector that provided
them was retired at the end of 2025, and even before that it only posted to
*channels* — never to a group chat. The supported route is a Power Automate flow:
**"When an HTTP request is received"** → **"Post message in a chat or channel"**.

Delivery is **inline with the poll** — each new headline is screened, scored and
posted as it arrives. Nothing on a schedule sweeps the database to send.

```
RSS ─► dedupe ─► kind ─► relevance ─► score ─► Teams ─► commit
                                    (skip if irrelevant)
```

Ordering has one wrinkle: the payload carries the headline's `id`, which only
exists once the row is in the database. So each row is `flush()`ed — staged in
the transaction, not yet durable — then delivered, then committed. The durable
write still lands after delivery.

Each headline is committed on its own rather than batching the poll, so an
interruption cannot leave delivered headlines unsaved. The residual risk is one
duplicate: if delivery succeeds and the commit then fails, the next poll sees
the headline as new. Duplicating a message beats dropping one.

A delivery failure never costs the headline. The row is saved either way with
`notified_at` left null, so failures stay visible and `POST /notify/retry` can
resend them. That endpoint is the only thing that reads the database to send,
and it is manual.

Before enabling for the first time, run `notify.suppress_backlog()` — otherwise
the entire stored corpus is delivered at once. Pass `before=<datetime>` to
suppress only older headlines and let recent ones still go out.

### Do not build the flow from a Microsoft template

Teams stamps every card posted by a template-derived flow with
*"<name> used a Workflow template to send this card. Get template"*. It comes
from the `template.id` in the flow's "Do Not Remove FlowIL" node, not from the
payload, so nothing in the JSON removes it.

Fix: **Save As** the flow in Power Automate and use the copy, which is not
linked to the template. Or create a blank flow -- Automated cloud flow, skip the
template picker, trigger *When an HTTP request is received*, action *Post card
in a chat or channel*. Either way the copy gets its own webhook URL, and the
original should be switched off so there are not two live endpoints.

### Trigger authentication

The flow's HTTP trigger has two modes, and the difference decides whether this
works at all:

| Mode | URL looks like | Config needed |
|---|---|---|
| Anonymous *(recommended)* | ends `...invoke?api-version=1&sp=…&sv=…&sig=…` | just `TEAMS_WEBHOOK_URL` |
| Entra OAuth | ends `...invoke?api-version=1` with no `sig` | `TEAMS_TENANT_ID`, `TEAMS_CLIENT_ID`, `TEAMS_CLIENT_SECRET` |

A URL with no `sig` that returns `DirectApiAuthorizationRequired` is in OAuth
mode with no credentials set. The fix is normally to open the trigger's advanced
settings and allow anyone with the URL to call it, then copy the **full** URL —
the signature parameters are what authenticate the call.

Tokens are fetched via client credentials and cached until shortly before expiry
when OAuth mode is used. A 401 is never retried, since retrying cannot fix a
credential problem.

The URL's `sig` parameter **is** the credential. httpx logs full request URLs at
INFO, which would write it into the deployment logs on every send, so the httpx
logger is pinned to WARNING and `notify.redact()` masks it anywhere a URL is
surfaced.

### Payload

The Workflows template **only accepts an Adaptive Card or MessageCard envelope**.
A plain `{"text": ...}` body is accepted by the trigger with a 202 and then
fails the run, which is a confusing way to find this out. So the payload is the
card itself and the flow is a dumb pipe.

The card is deliberately minimal — the headline, and a score line when the
headline scored:

```json
{
  "type": "message",
  "attachments": [{
    "contentType": "application/vnd.microsoft.card.adaptive",
    "content": {
      "type": "AdaptiveCard", "version": "1.4",
      "body": [
        {"type": "TextBlock", "text": "Trump: A lot of oil is pouring out of Hormuz", "wrap": true},
        {"type": "TextBlock", "text": "BULLISH  +36", "color": "Good", "weight": "Bolder"}
      ]
    }
  }]
}
```

Both blocks set `wrap` — headlines run long and are truncated without it. The
card carries no sender line: the chat already shows who posted it.

The score line is green for bullish, red for bearish. An unscored headline gets
**no score line at all** rather than a rendered `+0`, which would be
indistinguishable from a genuinely balanced reading.

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

Defaults to SQLite at `./data/cns.db`; set `DATABASE_URL` to use Postgres. The
provider-style `postgres://` scheme is accepted and given a driver automatically.
Then:

```bash
curl localhost:8000/health          # poll health, for Coolify
curl localhost:8000/stats           # counts, latest headline, failed polls
curl localhost:8000/headlines?limit=25
```

Tests: `.venv/Scripts/python -m pytest tests -q`

### Installing torch on Windows

`sentiment` mode needs FinBERT, so the `ml` extra has to be installed. On
Windows that fails from a deep project path — torch nests files far enough to
break the 260-character limit, and this project sits at 79 characters inside
OneDrive:

```
OSError: [WinError 206] The filename or extension is too long:
  ...\.venv\Lib\site-packages	orch-2.13.0+cpu.dist-info\licenses	hird_party  kineto\libkineto	hird_party\dynolog	hird_party\prometheus-cpp\...
```

Enabling `LongPathsEnabled` fixes it but needs admin and changes a system-wide
registry setting. A directory junction avoids both — it gives pip a short path
to install *through*, while the files land in the real venv:

```powershell
New-Item -ItemType Junction -Path C:\cns -Target "<project directory>"
C:\cns\.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
C:\cns\.venv\Scripts\python -m pip install numpy transformers
```

Afterwards `.venv` works normally from the original path; the junction is only
needed when installing. Linux and the Docker image have no such limit.

## Postgres layout

Tables live in a dedicated **`cns` schema**, not `public`, set by `DB_SCHEMA`.
The instance in use already holds ~65 unrelated tables in `public` (Refinitiv
1-minute bars for WTI `clc*`, Brent `lcoc*`, spreads and `esv1`), so keeping our
tables separate avoids any chance of a name collision.

The connection sets `search_path=cns,public`, so unqualified names resolve to
our tables while the price tables stay readable from the same session — a
backtest can join scores against bars without a cross-database query.

Note for stage 6: in those price tables `datetime` is already **UTC** (it matches
`datetime_ts` exactly), and `gmt_offset` is exchange metadata that is *not*
applied to it. Headlines are stored as naive UTC, so the two join directly.

## Live headlines for downstream consumers

`init_db()` installs a trigger, `headlines_notify`, that fires
`pg_notify('cns_headline', <id>)` on every stored headline that is `narrative`
and not `irrelevant`. A dashboard holding `LISTEN cns_headline` is woken the
moment a headline commits, instead of polling this database on a timer.

The payload is the row id alone. A headline's score is written to
`headline_scores` later in the same transaction, and NOTIFY is delivered at
COMMIT, so a listener that re-queries on wake always sees the two together --
a payload built at insert time would carry the headline without its score.

Consumers should re-query `id > last_seen` rather than the notified id. NOTIFY
is fire-and-forget with no queue, so that one choice also covers bursts and the
catch-up after a dropped connection. Two more things a consumer has to get
right, both of which are silent when wrong:

* Filter on `scorer_version`. `headline_scores` retains every version ever run,
  so an unfiltered join returns a headline once per version.
* `LEFT JOIN` the scores. A headline the scorer could not read has no score row
  at all, which is deliberately distinct from one scored as neutral.

Installing the trigger is best-effort: a database user without rights to create
functions gets a warning and a working pipeline, not a refusal to start.

## Deploying to Coolify

Create a **Docker Compose** resource pointing at this repo. No Postgres ships
with the stack -- `DATABASE_URL` must point at an existing instance, and compose
refuses to start without it rather than falling back to SQLite inside the
container, where the data would not survive a redeploy.

Set the environment variables *before* the first deploy. Compose interpolates
`DATABASE_URL` when the resource is parsed, so a missing value fails at save
time, not at container start. The service reads `.env` through `env_file`, so
every setting in `.env.example` reaches the container without needing its own
line in `docker-compose.yml`.

| Variable | Notes |
|---|---|
| `DATABASE_URL` | required; `postgresql+psycopg://user:pass@host:5432/dbname` |
| `DB_SCHEMA` | defaults to `cns` |
| `SCORER_VERSION` | must match the version the stored scores were written under, or `/index` reads an empty history |
| `TEAMS_ENABLED`, `TEAMS_WEBHOOK_URL` | delivery is off without both |
| `POLL_INTERVAL_SECONDS` | defaults to `61`; see the rate limit note in `sources/financial_juice.py` |
| `LOG_LEVEL` | defaults to `INFO` |

Leave `API_PORT` at `8000`. The image's healthcheck and published port are both
8000, so changing it makes the container permanently unhealthy.

`INSTALL_ML` is a **build** arg, not runtime config, and defaults to `1`. Setting
it as an ordinary Coolify environment variable has no effect on the image -- it
has to be flagged as a build variable. With torch absent, `SCORER_MODE=sentiment`
scores nothing at all; the app logs an error on startup saying so.

Point Coolify's healthcheck at `/health`; it reports `degraded` only when the
last three polls all failed, so a single transient 429 will not restart the
container. The first poll runs as a scheduled job rather than inline, so the app
answers `/health` immediately while FinBERT loads behind it -- on a cold cache
that download is ~450MB into the `modelcache` volume, and blocking startup on it
would trip the healthcheck and restart the container mid-download.

Budget ~3GB of disk for the image and ~2GB of RAM at runtime for torch.

**Run exactly one instance.** The rate limit is per IP and the poller assumes it is
the only writer — two replicas would 429 each other continuously.

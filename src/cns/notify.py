"""Post headlines to a Microsoft Teams group chat via Power Automate.

Teams incoming webhooks are not an option here. The Office 365 connector that
provided them was retired at the end of 2025, and even before that it only ever
posted to *channels* -- never to a group chat. The supported route is a Power
Automate flow with a "When an HTTP request is received" trigger feeding
"Post message in a chat or channel".

That trigger has two authentication modes, and both are supported here:

* **Anonymous (recommended).** The flow's trigger is set so anyone with the URL
  can call it. The URL then carries `sp`, `sv` and `sig` query parameters that
  authenticate the call, and no header is needed.
* **Entra OAuth.** The trigger requires a bearer token. Set the tenant/client
  credentials and a token is fetched and cached automatically.

A URL without `sig` that returns `DirectApiAuthorizationRequired` is in the
second mode with no credentials configured -- see the README.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from types import SimpleNamespace

import httpx

from .config import settings

log = logging.getLogger(__name__)

# The webhook URL's `sig` parameter is a credential. httpx logs full request
# URLs at INFO, so quieten it here too -- this module is importable from
# scripts that set up their own logging.
logging.getLogger("httpx").setLevel(logging.WARNING)


def redact(url: str) -> str:
    """Webhook URL with its signature masked, safe to log or return in an API."""
    return re.sub(r"sig=[^&]+", "sig=<redacted>", url or "")

#: Retried; anything else is treated as permanent and not retried.
_RETRY_STATUS = {408, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3

#: Refresh a token this many seconds before it actually expires.
_TOKEN_SKEW = 120

_token: str | None = None
_token_expires_at: float = 0.0


class NotifyError(RuntimeError):
    pass


@dataclass
class DeliveryResult:
    sent: int
    failed: int
    skipped: bool = False
    error: str | None = None


def is_configured() -> bool:
    return bool(settings.teams_enabled and settings.teams_webhook_url)


def _bearer_token() -> str | None:
    """Client-credentials token for the OAuth trigger mode, cached until expiry."""
    global _token, _token_expires_at

    if settings.teams_bearer_token:
        return settings.teams_bearer_token
    if not (settings.teams_tenant_id and settings.teams_client_id and settings.teams_client_secret):
        return None

    if _token and time.time() < _token_expires_at:
        return _token

    url = f"https://login.microsoftonline.com/{settings.teams_tenant_id}/oauth2/v2.0/token"
    resp = httpx.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.teams_client_id,
            "client_secret": settings.teams_client_secret,
            "scope": settings.teams_scope,
        },
        timeout=settings.http_timeout_seconds,
    )
    resp.raise_for_status()
    payload = resp.json()
    _token = payload["access_token"]
    _token_expires_at = time.time() + int(payload.get("expires_in", 3600)) - _TOKEN_SKEW
    log.info("acquired Teams bearer token")
    return _token


#: Direction -> Adaptive Card colour. Teams accepts only this fixed vocabulary.
_DIRECTION_COLOUR = {"bullish": "Good", "bearish": "Attention", "neutral": "Default"}

#: Strength meter geometry. Ten cells, so one cell is ten points of |value|.
_METER_CELLS = 10
_METER_STEP = 100 / _METER_CELLS
_METER_FILLED = "▰"
_METER_EMPTY = "▱"


def _meter(value: float) -> str:
    """Ten-cell bar for the *magnitude* of a score.

    The card used to print the raw `Score.value`, which is
    `direction * magnitude * damp * 100`. Its sign therefore only repeated the
    word beside it, and its digits were how hard the headline hits -- not a
    position on a bear-to-bull axis. Readers took "+72" for "72% bullish". A bar
    carries the one thing the number was actually saying and cannot be read as a
    percentage of anything directional; direction stays in the word and the
    colour, which is where it was never ambiguous.

    Deliberately not the confidence: that is a separate axis (see
    `scoring.Score`) and putting two bars on a chat card reads as one scale
    split in half.

    A non-zero score always fills at least one cell. The hybrid scorer never
    returns neutral by design, so an empty bar would read as "no reading" --
    which is what an absent score line already means.
    """
    filled = min(int(abs(value) // _METER_STEP), _METER_CELLS)
    if filled == 0 and value:
        filled = 1
    return _METER_FILLED * filled + _METER_EMPTY * (_METER_CELLS - filled)


def _as_score(stored):
    """Adapt a stored `HeadlineScore` row to what `build_payload` reads.

    The row keeps `label`/`score`; the card wants `direction`/`value`. Kept as
    a shim rather than renaming either, because the column names are what the
    scorer-comparison queries are written against.
    """
    if stored is None:
        return None
    return SimpleNamespace(direction=stored.label or "neutral", value=stored.score)


def build_payload(headline, score=None, index=None) -> dict:
    """Build the Adaptive Card posted to the flow.

    The Workflows "post a card when a webhook request is received" template only
    accepts an Adaptive Card or MessageCard envelope -- a plain {"text": ...}
    body is accepted by the trigger (202) and then fails the run. So the payload
    is the card itself, and the flow is a dumb pipe.

    Two lines at most: the headline, and the score when there is one. The score
    line is the direction word plus a `_meter` bar of the magnitude -- never the
    signed number, which readers took for a bull/bear percentage.

    An unscored headline gets no score line at all rather than an empty bar,
    which would be indistinguishable from a genuinely balanced reading.
    """
    body = [
        {
            "type": "TextBlock",
            "text": headline.title,
            "wrap": True,
            "spacing": "None",
        },
    ]

    if score is not None:
        body.append(
            {
                "type": "TextBlock",
                "text": "%s  %s  strength" % (
                    score.direction.upper(),
                    _meter(score.value),
                ),
                "weight": "Bolder",
                "color": _DIRECTION_COLOUR.get(score.direction, "Default"),
                "wrap": True,
                "spacing": "Small",
            }
        )

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                },
            }
        ],
    }


def post(payload: dict) -> None:
    """POST one payload to the flow, retrying transient failures."""
    if not settings.teams_webhook_url:
        raise NotifyError("TEAMS_WEBHOOK_URL is not set")

    headers = {"Content-Type": "application/json"}
    token = _bearer_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last: str | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = httpx.post(
                settings.teams_webhook_url,
                json=payload,
                headers=headers,
                timeout=settings.http_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            last = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code < 300:
                return
            last = f"http {resp.status_code}: {resp.text[:200]}"
            if resp.status_code == 401:
                # Credentials or trigger mode are wrong; retrying cannot help.
                raise NotifyError(
                    "Teams rejected the call (401). The flow's HTTP trigger requires "
                    "OAuth but no credentials are configured, or the webhook URL is "
                    f"missing its sig/sp/sv parameters. {last}"
                )
            if resp.status_code not in _RETRY_STATUS:
                raise NotifyError(last)

        if attempt < _MAX_ATTEMPTS:
            time.sleep(2 ** attempt)

    raise NotifyError(f"giving up after {_MAX_ATTEMPTS} attempts: {last}")


def send_pending(limit: int | None = None, dry_run: bool = False) -> DeliveryResult:
    """Recovery path: post relevant headlines whose inline delivery failed.

    **Not** part of the normal flow. Headlines are delivered inline as they are
    polled (see cns.poller); nothing on a schedule sweeps the database to send.
    This exists so that a Teams outage does not permanently swallow whatever
    headlines it happened to cover, and is only reachable via POST /notify/retry.

    Capped per call so a backlog cannot dump fifty messages into the chat.
    """
    from sqlalchemy import select

    from . import scoring
    from .classify import NARRATIVE
    from .db import SessionLocal
    from .models import Headline, HeadlineScore, utcnow
    from .relevance import IRRELEVANT

    if not is_configured() and not dry_run:
        return DeliveryResult(0, 0, skipped=True, error="Teams delivery not configured")

    cap = limit or settings.teams_max_per_run
    sent = failed = 0
    with SessionLocal() as session:
        # Pull the stored score alongside the headline. This path used to post
        # the title on its own: `build_payload` defaults `score` to None, and
        # the poller's inline send was the only caller passing one. Anything
        # delivered from the backlog -- which is everything, after a restart --
        # arrived in the chat with no direction on it.
        #
        # Outer join, so a headline that somehow has no score for the current
        # version still gets delivered rather than silently held back.
        pending = session.execute(
            select(Headline, HeadlineScore)
            .outerjoin(
                HeadlineScore,
                (HeadlineScore.headline_id == Headline.id)
                & (HeadlineScore.scorer_version == scoring.version()),
            )
            .where(
                Headline.notified_at.is_(None),
                Headline.kind == NARRATIVE,
                Headline.category != IRRELEVANT,
            )
            .order_by(Headline.published_at.asc())
            .limit(cap)
        ).all()
        for headline, stored in pending:
            payload = build_payload(headline, score=_as_score(stored))
            if dry_run:
                log.info("[dry-run] would post: %s", headline.title)
                sent += 1
                continue
            try:
                post(payload)
            except NotifyError as exc:
                log.error("Teams delivery failed for headline %s: %s", headline.id, exc)
                failed += 1
                # Stop on the first failure rather than burning through the
                # backlog against a broken endpoint.
                break
            headline.notified_at = utcnow()
            sent += 1
        if not dry_run:
            session.commit()

    return DeliveryResult(sent=sent, failed=failed)


def suppress_backlog(before=None) -> int:
    """Mark stored headlines as already delivered, so they are never posted.

    Run before enabling delivery for the first time, or switching it on replays
    the entire corpus into the chat. Pass `before` to suppress only headlines
    older than that instant and let anything more recent still go out.
    """
    from sqlalchemy import update

    from .classify import NARRATIVE
    from .db import SessionLocal
    from .models import Headline, utcnow
    from .relevance import IRRELEVANT

    conditions = [
        Headline.notified_at.is_(None),
        Headline.kind == NARRATIVE,
        Headline.category != IRRELEVANT,
    ]
    if before is not None:
        conditions.append(Headline.published_at < before)

    with SessionLocal() as session:
        result = session.execute(
            update(Headline).where(*conditions).values(notified_at=utcnow())
        )
        session.commit()
        return result.rowcount

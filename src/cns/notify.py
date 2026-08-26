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

_CATEGORY_LABEL = {"oil_direct": "Crude oil", "geo_risk": "Geopolitics"}


def build_payload(headline, score=None, index=None) -> dict:
    """Build the Adaptive Card posted to the flow.

    The Workflows "post a card when a webhook request is received" template only
    accepts an Adaptive Card or MessageCard envelope -- a plain {"text": ...}
    body is accepted by the trigger (202) and then fails the run. So the payload
    is the card itself, and the flow is a dumb pipe.
    """
    terms = headline.relevance_terms.split(",") if headline.relevance_terms else []
    label = _CATEGORY_LABEL.get(headline.category, headline.category)

    body = [
        {
            "type": "TextBlock",
            "text": label.upper(),
            "weight": "Bolder",
            "size": "Small",
            "color": "Accent",
            "spacing": "None",
        },
        {
            "type": "TextBlock",
            "text": headline.title,
            "wrap": True,
            "size": "Medium",
            "weight": "Bolder",
        },
    ]

    if score is not None:
        body.append(
            {
                "type": "TextBlock",
                "text": f"{score.direction.upper()}  {score.value:+.0f}",
                "weight": "Bolder",
                "size": "Large",
                "color": _DIRECTION_COLOUR.get(score.direction, "Default"),
                "spacing": "Small",
            }
        )

    facts = []
    if score is not None:
        facts.append({"title": "Confidence", "value": f"{score.confidence:.0%}"})
        if score.event:
            facts.append({"title": "Event", "value": score.event})
    if index is not None:
        facts.append({"title": "7-day index", "value": f"{index:+.1f}"})
    if terms:
        facts.append({"title": "Matched", "value": ", ".join(terms[:6])})
    if headline.published_at is not None:
        facts.append(
            {"title": "Published", "value": headline.published_at.strftime("%Y-%m-%d %H:%M UTC")}
        )
    if facts:
        body.append({"type": "FactSet", "facts": facts, "spacing": "Small"})

    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    # Action.OpenUrl rejects a null url, which fails the whole card.
    if headline.link:
        card["actions"] = [
            {"type": "Action.OpenUrl", "title": "Read on FinancialJuice", "url": headline.link}
        ]

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
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

    from .classify import NARRATIVE
    from .db import SessionLocal
    from .models import Headline, utcnow
    from .relevance import IRRELEVANT

    if not is_configured() and not dry_run:
        return DeliveryResult(0, 0, skipped=True, error="Teams delivery not configured")

    cap = limit or settings.teams_max_per_run
    sent = failed = 0
    with SessionLocal() as session:
        pending = list(
            session.scalars(
                select(Headline)
                .where(
                    Headline.notified_at.is_(None),
                    Headline.kind == NARRATIVE,
                    Headline.category != IRRELEVANT,
                )
                .order_by(Headline.published_at.asc())
                .limit(cap)
            )
        )
        for headline in pending:
            payload = build_payload(headline)
            if dry_run:
                log.info("[dry-run] would post: %s", payload["text"].replace("\n", " | "))
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

"""FinancialJuice RSS source.

The feed sits behind Cloudflare and rate-limits to roughly one request per
minute per IP: a second request inside that window returns HTTP 429 with a
`Retry-After` header (observed 41-60s) and the body `error code: 1015`.
That is why the poll interval is measured in tens of seconds, not seconds, and
why POLL_INTERVAL_SECONDS should not go below ~61.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import httpx

from ..config import settings

log = logging.getLogger(__name__)

SOURCE_NAME = "financialjuice"
TITLE_PREFIX = "FinancialJuice:"

#: Fallback when a 429 arrives without a Retry-After header.
DEFAULT_RETRY_AFTER_SECONDS = 60


def _should_retry(retry_after: int) -> bool:
    """Whether retrying inside this poll beats waiting for the next tick.

    Retrying blocks the job for the whole wait. That was worth it at a 90s
    interval, where a dropped poll cost 90s of headlines. At a 61s interval it
    is actively harmful: a 60s sleep outlasts the next scheduled tick, which
    APScheduler then skips (max_instances=1), so one 429 turns a 61s cadence
    into ~122s. Only retry when the wait finishes comfortably before the next
    poll would have run anyway.
    """
    return retry_after + 2 < settings.poll_interval_seconds * 0.5


@dataclass(frozen=True)
class FeedItem:
    external_id: str
    title: str
    raw_title: str
    link: str | None
    published_at: datetime | None


@dataclass
class FetchResult:
    items: list[FeedItem]
    status_code: int | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class RateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


def _clean_title(raw: str) -> str:
    title = raw.strip()
    if title.startswith(TITLE_PREFIX):
        title = title[len(TITLE_PREFIX):].strip()
    return title


def _parse_published(entry) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None)
    if not parsed:
        return None
    # feedparser returns a UTC struct_time; store naive UTC.
    return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc).replace(tzinfo=None)


def _external_id(entry) -> str | None:
    guid = getattr(entry, "id", None) or getattr(entry, "guid", None)
    if guid:
        return str(guid).strip()
    link = getattr(entry, "link", None)
    return str(link).strip() if link else None


def parse(xml: str) -> list[FeedItem]:
    parsed = feedparser.parse(xml)
    items: list[FeedItem] = []
    for entry in parsed.entries:
        ext_id = _external_id(entry)
        raw_title = (getattr(entry, "title", "") or "").strip()
        if not ext_id or not raw_title:
            continue
        items.append(
            FeedItem(
                external_id=ext_id,
                title=_clean_title(raw_title),
                raw_title=raw_title,
                link=getattr(entry, "link", None),
                published_at=_parse_published(entry),
            )
        )
    return items


def _get(client: httpx.Client) -> str:
    resp = client.get(settings.feed_url)
    if resp.status_code == 429:
        raise RateLimited(int(resp.headers.get("Retry-After", DEFAULT_RETRY_AFTER_SECONDS)))
    resp.raise_for_status()
    return resp.text


def fetch() -> FetchResult:
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    }
    try:
        with httpx.Client(
            timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True
        ) as client:
            try:
                xml = _get(client)
            except RateLimited as exc:
                if not _should_retry(exc.retry_after):
                    log.warning(
                        "rate limited (retry_after=%ss); waiting for the next poll",
                        exc.retry_after,
                    )
                    return FetchResult([], 429, f"rate limited, retry_after={exc.retry_after}s")
                log.warning("rate limited; retrying once in %ss", exc.retry_after + 2)
                time.sleep(exc.retry_after + 2)
                xml = _get(client)
    except RateLimited as exc:
        return FetchResult([], 429, f"rate limited after retry, retry_after={exc.retry_after}s")
    except httpx.HTTPStatusError as exc:
        return FetchResult([], exc.response.status_code, f"http {exc.response.status_code}")
    except httpx.HTTPError as exc:
        return FetchResult([], None, f"{type(exc).__name__}: {exc}")

    return FetchResult(parse(xml), 200)

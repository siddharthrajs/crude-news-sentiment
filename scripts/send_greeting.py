"""Post a greeting to the Teams group chat, to check the webhook end to end.

Usage:
    python scripts/send_greeting.py
    python scripts/send_greeting.py "Custom message"
    python scripts/send_greeting.py "Custom message" https://example.com

Reads TEAMS_WEBHOOK_URL from .env. Nothing is written to the database -- this
only exercises the webhook.
"""

import sys

from cns import notify
from cns.config import settings

DEFAULT_MESSAGE = "Hello from crude-news-sentiment"
DEFAULT_URL = "https://www.financialjuice.com/"


def main() -> int:
    message = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MESSAGE
    url = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_URL

    if not settings.teams_webhook_url:
        print("TEAMS_WEBHOOK_URL is not set in .env")
        return 1

    # Same payload shape the pipeline sends, so this exercises the real flow
    # rather than a special case the flow might handle differently.
    payload = {
        "text": f"**{message}**\n{url}",
        "headline": message,
        "category": "test",
        "label": "Test",
        "matched_terms": [],
        "url": url,
        "published_at": None,
        "headline_id": None,
        "score": None,
        "direction": None,
        "confidence": None,
        "event": None,
        "market_index": None,
    }

    print("posting to :", notify.redact(settings.teams_webhook_url))
    print("message    :", message)
    print("url        :", url)

    try:
        notify.post(payload)
    except notify.NotifyError as exc:
        print("\nFAILED:", exc)
        return 1

    print("\nAccepted by the flow. Check the Teams chat to confirm it arrived --")
    print("a 2xx means the flow started, not that the message posted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

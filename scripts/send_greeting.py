"""Post a greeting to the Teams group chat, to check the webhook end to end.

Usage:
    python scripts/send_greeting.py
    python scripts/send_greeting.py "Custom message"
    python scripts/send_greeting.py "Custom message" https://example.com
    python scripts/send_greeting.py --simple      # bare {"text": ...} only

`--simple` exists to isolate flow failures. The full payload sends nulls for the
score fields, which fails validation if the trigger's Request Body JSON Schema
declares them as string or integer. If --simple works and the default does not,
the schema is the problem: clear it, or regenerate it from the full payload this
script prints.

Reads TEAMS_WEBHOOK_URL from .env. Nothing is written to the database -- this
only exercises the webhook.
"""

import json
import sys

from cns import notify
from cns.config import settings

DEFAULT_MESSAGE = "Hello from crude-news-sentiment"
DEFAULT_URL = "https://www.financialjuice.com/"


def _full_payload(message: str, url: str) -> dict:
    """Exactly the shape the pipeline sends, nulls included."""
    return {
        "text": "**" + message + "**\n" + url,
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


def main() -> int:
    simple = "--simple" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--simple"]
    message = args[0] if args else DEFAULT_MESSAGE
    url = args[1] if len(args) > 1 else DEFAULT_URL

    if not settings.teams_webhook_url:
        print("TEAMS_WEBHOOK_URL is not set in .env")
        return 1

    if simple:
        payload = {"text": "**" + message + "**\n" + url}
    else:
        payload = _full_payload(message, url)

    print("posting to :", notify.redact(settings.teams_webhook_url))
    print("shape      :", "simple (text only)" if simple else "full pipeline payload")
    print("payload    :")
    print(json.dumps(payload, indent=2))

    try:
        notify.post(payload)
    except notify.NotifyError as exc:
        print()
        print("FAILED:", exc)
        return 1

    print()
    print("Accepted by the flow -- but that only means the flow STARTED.")
    print("Check the run history in Power Automate for the real outcome.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

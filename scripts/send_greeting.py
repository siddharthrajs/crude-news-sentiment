"""Post a greeting card to the Teams group chat, to check the webhook end to end.

Usage:
    python scripts/send_greeting.py
    python scripts/send_greeting.py "Custom message"
    python scripts/send_greeting.py "Custom message" https://example.com

The Workflows "post a card when a webhook request is received" template only
accepts an Adaptive Card or MessageCard envelope. A plain {"text": ...} body is
accepted by the trigger (HTTP 202) and then fails the run, which is why this
sends a real card -- the same envelope the pipeline uses.

Reads TEAMS_WEBHOOK_URL from .env. Nothing is written to the database.
"""

import json
import sys
from datetime import datetime, timezone

from cns import notify
from cns.config import settings

DEFAULT_MESSAGE = "Hello from crude-news-sentiment"
DEFAULT_URL = "https://www.financialjuice.com/"


class _Greeting:
    """Quacks like a Headline, so the real card builder is exercised."""

    id = None
    category = "oil_direct"
    relevance_terms = "test"

    def __init__(self, title, link):
        self.title = title
        self.link = link
        self.published_at = datetime.now(timezone.utc).replace(tzinfo=None)


def main() -> int:
    args = sys.argv[1:]
    message = args[0] if args else DEFAULT_MESSAGE
    url = args[1] if len(args) > 1 else DEFAULT_URL

    if not settings.teams_webhook_url:
        print("TEAMS_WEBHOOK_URL is not set in .env")
        return 1

    payload = notify.build_payload(_Greeting(message, url))

    print("posting to :", notify.redact(settings.teams_webhook_url))
    print("payload    :")
    print(json.dumps(payload, indent=2))

    try:
        notify.post(payload)
    except notify.NotifyError as exc:
        print()
        print("FAILED:", exc)
        return 1

    print()
    print("Accepted. Check the Teams chat, and the run history in Power Automate")
    print("if it does not appear -- a 2xx only means the flow started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

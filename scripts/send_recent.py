"""Manually post recent crude-relevant headlines to Teams as Adaptive Cards.

Usage:
    python scripts/send_recent.py                    # last 6 hours, dry run
    python scripts/send_recent.py --send             # actually post them
    python scripts/send_recent.py --hours 12 --send
    python scripts/send_recent.py --category oil_direct --send

Dry run by default: this posts into a shared group chat, so the default should
not be the one that surprises people.

Sends oldest-first so the chat reads in the order events happened, and marks
each row `notified_at` so the running pipeline will not repeat them.
"""

import argparse
import sys
from datetime import timedelta

from sqlalchemy import select

from cns import notify, scoring
from cns.db import SessionLocal
from cns.models import Headline, utcnow


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument(
        "--category",
        choices=["all", "oil_direct", "geo_risk"],
        default="all",
        help="'all' means both relevant categories (the default).",
    )
    parser.add_argument("--send", action="store_true", help="post for real")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.send and not notify.settings.teams_webhook_url:
        print("TEAMS_WEBHOOK_URL is not set in .env")
        return 1

    cutoff = utcnow() - timedelta(hours=args.hours)
    conditions = [
        Headline.published_at >= cutoff,
        Headline.kind == "narrative",
        Headline.category != "irrelevant",
    ]
    if args.category != "all":
        conditions.append(Headline.category == args.category)

    with SessionLocal() as session:
        rows = list(
            session.scalars(
                select(Headline).where(*conditions).order_by(Headline.published_at.asc())
            )
        )

        print(f"window   : last {args.hours:g}h (since {cutoff:%Y-%m-%d %H:%M} UTC)")
        print(f"category : {args.category}")
        print(f"matched  : {len(rows)} headlines")
        print(f"mode     : {'SENDING' if args.send else 'dry run -- pass --send to post'}")
        print()

        sent = failed = 0
        for row in rows:
            line = f"  {row.published_at:%H:%M}  {row.category:<10}  {row.title[:66]}"
            if not args.send:
                print(line)
                continue
            try:
                notify.post(notify.build_payload(row, score=scoring.score(row)))
            except notify.NotifyError as exc:
                print(f"  FAILED {line.strip()}\n         {exc}")
                failed += 1
                continue
            row.notified_at = utcnow()
            session.commit()
            sent += 1
            print(f"  sent   {line.strip()}")

    if args.send:
        print()
        print(f"sent {sent}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""Score stored headlines that have no score for the current scorer version.

Usage:
    python scripts/rescore.py            # report what would change
    python scripts/rescore.py --write

Scores are keyed on (headline_id, scorer_version), so bumping SCORER_VERSION and
re-running produces a second opinion alongside the old one rather than
overwriting it. That is what makes two scorer versions comparable on identical
input.
"""

import argparse
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from sqlalchemy import select  # noqa: E402

from cns import scoring  # noqa: E402
from cns.config import settings  # noqa: E402
from cns.db import SessionLocal, init_db  # noqa: E402
from cns.models import Headline, HeadlineScore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    init_db()
    print("scorer version :", scoring.version())
    print("mode           :", settings.scorer_mode)
    print("finbert        :", "on" if scoring.is_available() and settings.finbert_enabled else "rules only")
    print()

    with SessionLocal() as session:
        already = set(
            session.scalars(
                select(HeadlineScore.headline_id).where(
                    HeadlineScore.scorer_version == scoring.version()
                )
            )
        )
        pending = [
            h
            for h in session.scalars(
                select(Headline)
                .where(Headline.kind == "narrative", Headline.category != "irrelevant")
                .order_by(Headline.published_at.asc())
            )
            if h.id not in already
        ]

        scored = skipped = 0
        for headline in pending:
            result = scoring.score(headline)
            if result is None:
                skipped += 1
                continue
            scored += 1
            print("  %-8s %+6.1f  %-12s conf %.2f | %s"
                  % (result.direction, result.value, result.event,
                     result.confidence, headline.title[:56]))
            if args.write:
                session.add(
                    HeadlineScore(
                        headline_id=headline.id,
                        scorer_version=scoring.version(),
                        category=headline.category,
                        score=result.value,
                        confidence=result.confidence,
                        label=result.direction,
                        components=result.components,
                    )
                )
        if args.write:
            session.commit()

    print()
    print("candidates %d | scored %d | no event found %d"
          % (len(pending), scored, skipped))
    if not args.write:
        print("dry run -- pass --write to persist")
    return 0


if __name__ == "__main__":
    sys.exit(main())

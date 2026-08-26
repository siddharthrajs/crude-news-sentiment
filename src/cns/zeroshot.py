"""Zero-shot relevance classifier, as a second opinion alongside the lexicon.

The lexicon in `cns.relevance` is fast and precise on domain vocabulary (bbl,
Cushing, OPEC) but blind to anything phrased in words it does not list. This
reads meaning instead, so it can catch "Kazakhstan's CPC terminal halts
loadings" -- a headline with no lexicon term in it at all.

Neither is trusted over the other. Both verdicts are stored, and the cases where
they disagree are the point: those are the headlines worth reading by hand, and
they are how the labelled set grows beyond what the lexicon already knows.

Torch and transformers are optional. If they are not installed this module
reports unavailable and the pipeline runs lexicon-only.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from .config import settings
from .relevance import GEO_RISK, IRRELEVANT, OIL_DIRECT

log = logging.getLogger(__name__)

#: Natural-language descriptions of each category. The model compares the
#: headline against each of these, so the wording matters as much as any
#: threshold -- these were chosen over terser labels like "oil" or "war",
#: which pulled in far too much.
HYPOTHESES: dict[str, str] = {
    OIL_DIRECT: "crude oil supply, demand, prices, refining or shipping",
    GEO_RISK: "war, military conflict, sanctions or political crisis involving an oil-producing country",
    IRRELEVANT: "routine business, economic data, central banks, technology or domestic politics",
}

#: Sent to the model in this order; index maps back through _LABELS.
_LABELS = list(HYPOTHESES)
_TEMPLATE = "This news headline is about {}."

_pipeline = None
_lock = threading.Lock()


@dataclass(frozen=True)
class ZeroShotVerdict:
    category: str
    score: float
    #: Score for every category, so a decision stays auditable.
    scores: dict[str, float]


class Unavailable(RuntimeError):
    """torch/transformers are not installed."""


def is_available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


def _load():
    """Build the pipeline once. ~1.6GB resident, ~15s cold start."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    with _lock:
        if _pipeline is not None:
            return _pipeline
        try:
            from transformers import pipeline
        except ImportError as exc:
            raise Unavailable(
                "zero-shot needs torch and transformers: pip install -e '.[ml]'"
            ) from exc
        log.info("loading zero-shot model %s (first run downloads ~1.6GB)", settings.zeroshot_model)
        _pipeline = pipeline(
            "zero-shot-classification",
            model=settings.zeroshot_model,
            device=-1,  # CPU: headlines are short and volume is ~160/day.
        )
        log.info("zero-shot model ready")
    return _pipeline


def classify(title: str) -> ZeroShotVerdict:
    return classify_batch([title])[0]


def classify_batch(titles: list[str]) -> list[ZeroShotVerdict]:
    """Classify several headlines at once.

    `multi_label=False` makes the scores a softmax across the three hypotheses,
    so they sum to 1 and can be compared against a single threshold.
    """
    if not titles:
        return []

    clf = _load()
    raw = clf(
        titles,
        candidate_labels=[HYPOTHESES[label] for label in _LABELS],
        hypothesis_template=_TEMPLATE,
        multi_label=False,
    )
    if isinstance(raw, dict):  # a single input returns a bare dict
        raw = [raw]

    hypothesis_to_label = {HYPOTHESES[label]: label for label in _LABELS}
    verdicts = []
    for item in raw:
        scores = {
            hypothesis_to_label[hypothesis]: float(score)
            for hypothesis, score in zip(item["labels"], item["scores"])
        }
        # Relevance is the question, not which of the two relevant categories
        # wins. The model routinely splits its mass across oil and geo -- "Trump
        # nuclear deal with Saudi Arabia" came back geo 0.48 / oil 0.44 -- so a
        # top-1 rule would call that noise despite 0.92 of the mass saying it is
        # not. Decide on the combined relevant mass, then pick the larger side.
        relevant_mass = scores[OIL_DIRECT] + scores[GEO_RISK]
        if relevant_mass >= settings.zeroshot_threshold:
            best = OIL_DIRECT if scores[OIL_DIRECT] >= scores[GEO_RISK] else GEO_RISK
            confidence = relevant_mass
        else:
            best = IRRELEVANT
            confidence = scores[IRRELEVANT]
        verdicts.append(ZeroShotVerdict(best, round(confidence, 4), scores))
    return verdicts


def score_pending(limit: int | None = None) -> int:
    """Add a zero-shot verdict to headlines that lack one. Returns how many.

    Only narrative headlines are scored, matching what the lexicon relevance
    step operates on.

    Runs as its own job rather than inside the poll: the model takes ~15s to
    load and ~100ms per headline, and ingestion must not wait on either.
    """
    from sqlalchemy import select

    from .classify import NARRATIVE
    from .db import SessionLocal
    from .models import Headline, utcnow

    batch = limit or settings.zeroshot_batch_size
    with SessionLocal() as session:
        pending = list(
            session.scalars(
                select(Headline)
                .where(
                    Headline.zs_category.is_(None),
                    # Only narrative items. Calendar prints and widgets are
                    # excluded upstream by `kind`, so their lexicon category is
                    # forced to irrelevant regardless of content -- scoring them
                    # would manufacture disagreements that mean nothing.
                    Headline.kind == NARRATIVE,
                )
                .order_by(Headline.published_at.desc())
                .limit(batch)
            )
        )
        if not pending:
            return 0

        verdicts = classify_batch([h.title for h in pending])
        now = utcnow()
        for headline, verdict in zip(pending, verdicts):
            headline.zs_category = verdict.category
            headline.zs_score = verdict.score
            headline.zs_scored_at = now
        session.commit()

    log.info("zero-shot scored %d headlines", len(pending))
    return len(pending)

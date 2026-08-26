"""Score a headline as bullish or bearish for crude.

Two scorers, selected by ``SCORER_MODE``. Their scores are stored under
different versions (see `version()`), so switching modes adds a second opinion
rather than overwriting the first, and the two stay comparable on identical
headlines.

**sentiment** (default) -- FinBERT net sentiment, `P(positive) - P(negative)`,
scaled to +/-100. Judges wording only. Simple, and it reads the language rather
than a fixed vocabulary, so it handles phrasings no rule anticipated.

    Known limitation: sentiment is not price direction for commodities.
    Measured on this corpus, FinBERT rates "OPEC announces deep cuts" negative
    at 0.85 and "OPEC raises production quotas" positive at 0.69 -- inverted
    both times, because a supply cut sounds grim and lifts the price. Anything
    scored in this mode inherits that.

**event** -- direction from the supply/demand event in `cns.events`, magnitude
from entity weight and stated volumes, intensity from FinBERT. Immune to the
inversion above, but blind to phrasings its vocabulary does not cover, and it
abstains on roughly 60% of relevant headlines.

The published `Captain-1337/CrudeBERT` was meant to solve this properly and
cannot: it returns effectively the same distribution for every input (per-class
standard deviation [0.006, 0.064, 0.064] across our corpus). See
`scripts/eval_crudebert.py`.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from . import events
from .config import settings

log = logging.getLogger(__name__)

_model = None
_tokenizer = None
_lock = threading.Lock()

#: FinBERT output index -> label, from the checkpoint's own config, which for
#: this model is self-consistent (unlike CrudeBERT's).
_FINBERT_LABELS = {0: "positive", 1: "negative", 2: "neutral"}

DIRECTION_NAME = {1: "bullish", -1: "bearish", 0: "neutral"}

#: Below this much net sentiment the headline is called neutral rather than
#: given a direction on noise.
_NEUTRAL_BAND = 0.10


@dataclass(frozen=True)
class Score:
    #: Bearish -100 .. +100 bullish.
    value: float
    #: bullish | bearish | neutral
    direction: str
    #: 0..1, how much weight the index should give this.
    confidence: float
    #: What drove the call, for the audit trail.
    event: str | None = None
    #: Per-component breakdown.
    components: dict | None = None


def version() -> str:
    """Scorer identity for `headline_scores`.

    Derived from the mode as well as the configured version, so switching modes
    cannot silently mix two different scorers under one label.
    """
    suffix = "-inv" if (settings.scorer_mode == "sentiment" and settings.scorer_invert) else ""
    return f"{settings.scorer_version}-{settings.scorer_mode}{suffix}"


def is_available() -> bool:
    """Whether FinBERT can be loaded."""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


def _load():
    """Load FinBERT once. ~450MB resident, ~10s cold start."""
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer
    with _lock:
        if _model is not None:
            return _model, _tokenizer
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        log.info("loading %s", settings.finbert_model)
        _tokenizer = AutoTokenizer.from_pretrained(settings.finbert_model)
        _model = AutoModelForSequenceClassification.from_pretrained(settings.finbert_model)
        _model.eval()
        log.info("FinBERT ready")
    return _model, _tokenizer


def _finbert_probs(title: str) -> dict[str, float] | None:
    """Class probabilities, or None if the model is unavailable or fails."""
    if not is_available():
        return None
    try:
        import torch

        model, tokenizer = _load()
        with torch.no_grad():
            logits = model(**tokenizer(title, return_tensors="pt", truncation=True)).logits
        probs = torch.softmax(logits, dim=-1)[0].tolist()
    except Exception:
        log.exception("FinBERT scoring failed")
        return None
    return {_FINBERT_LABELS[i]: p for i, p in enumerate(probs)}


def _score_sentiment(title: str) -> Score | None:
    """Net sentiment of the wording, scaled to +/-100.

    With `SCORER_INVERT` on, the sign is flipped. The reasoning is that most
    oil-relevant news FinBERT reads as negative -- supply cuts, outages,
    sanctions, conflict, blocked tankers -- is bullish for crude, and most it
    reads as positive -- ceasefires, agreements, normalised shipping -- is
    bearish. Inverting therefore fixes the common case.

    It also breaks three cases, which no sign flip can fix:

    * **Demand news.** "Global oil demand collapses" is negative *and* bearish;
      inverting makes it bullish.
    * **Inventory builds.** A build is bearish and often reads neutral-positive.
    * **Explicit price headlines.** "Oil slides 3% on the session" is negative
      and bearish. Inverting turns a report of a fall into a bullish signal.

    The last one is the dangerous one, since price headlines are common and the
    inversion is confidently wrong on every one of them.
    """
    probs = _finbert_probs(title)
    if probs is None:
        log.warning("sentiment mode needs FinBERT; install the ml extra")
        return None

    net = probs["positive"] - probs["negative"]
    if settings.scorer_invert:
        net = -net
    # Neutral mass is the model saying "this wording carries no charge", which
    # is exactly how little the market should read into it.
    confidence = round(1.0 - probs["neutral"], 3)
    direction = 0 if abs(net) < _NEUTRAL_BAND else (1 if net > 0 else -1)

    return Score(
        value=round(net * 100, 1),
        direction=DIRECTION_NAME[direction],
        confidence=confidence,
        event="sentiment",
        components={
            "mode": "sentiment",
            "inverted": settings.scorer_invert,
            "finbert": {k: round(v, 4) for k, v in probs.items()},
            "net": round(net, 4),
        },
    )


def _score_event(title: str) -> Score | None:
    """Direction from the supply/demand event; FinBERT only sets intensity."""
    event = events.classify(title)
    if event.direction == 0:
        return None

    probs = _finbert_probs(title) if settings.finbert_enabled else None
    if probs is None:
        # Rules alone are directionally sound but blunt about strength.
        intensity, finbert_scores = 0.6, None
    else:
        intensity = probs["positive"] + probs["negative"]
        finbert_scores = {k: round(v, 4) for k, v in probs.items()}

    magnitude = event.magnitude * event.entity_weight * (0.5 + 0.5 * intensity)
    confidence = round(
        min(event.entity_weight * (0.6 + 0.4 * intensity) * (0.6 if event.hedged else 1.0), 1.0),
        3,
    )
    return Score(
        value=round(event.direction * magnitude * 100, 1),
        direction=DIRECTION_NAME[event.direction],
        confidence=confidence,
        event=event.kind,
        components={
            "mode": "event",
            "event": event.kind,
            "event_magnitude": round(event.magnitude, 3),
            "entity_weight": event.entity_weight,
            "intensity": round(intensity, 3),
            "hedged": event.hedged,
            "matched": event.matched,
            "finbert": finbert_scores,
        },
    )


def score(headline) -> Score | None:
    """Score one relevant headline, or None if it could not be scored.

    None is deliberate rather than a neutral 0.0: "could not be read" and
    "read as genuinely balanced" must stay distinguishable, or the index
    averages in placeholders as though they were real readings.
    """
    title = getattr(headline, "title", "") or ""
    if not title.strip():
        return None
    if settings.scorer_mode == "event":
        return _score_event(title)
    return _score_sentiment(title)

"""Score a headline as bullish or bearish for crude.

Two signals, with a strict division of labour:

* **Direction comes from the event** (`cns.events`), never from tone. A supply
  cut is bullish however grim the wording. This is the whole reason a general
  sentiment model cannot do this job alone -- measured on the live corpus,
  FinBERT rates "OPEC announces deep cuts" negative at 0.85 and "OPEC raises
  production quotas" positive at 0.69, inverted in both directions.

* **Intensity comes from FinBERT**, used only for *how strongly* the headline is
  worded, never for which way it points. Its confidence that a headline is
  charged at all is a reasonable proxy for how much the market will care.

The published `Captain-1337/CrudeBERT` was meant to fill this role and cannot:
it returns effectively the same distribution for every input (per-class standard
deviation [0.006, 0.064, 0.064] across our corpus, identical probabilities for
opposite-direction controls). `scripts/eval_crudebert.py` reproduces that.

FinBERT is optional. Without torch installed the scorer still runs on the rules
alone, at reduced confidence.
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

#: FinBERT output index -> label. Verified against the checkpoint's own config,
#: which for this model is consistent (unlike CrudeBERT's).
_FINBERT_LABELS = {0: "positive", 1: "negative", 2: "neutral"}

DIRECTION_NAME = {1: "bullish", -1: "bearish", 0: "neutral"}


@dataclass(frozen=True)
class Score:
    #: Bearish -100 .. +100 bullish.
    value: float
    #: bullish | bearish | neutral
    direction: str
    #: 0..1, how much weight the index should give this.
    confidence: float
    #: Supply/demand event behind the call, for the audit trail.
    event: str | None = None
    #: Per-component breakdown.
    components: dict | None = None


def is_available() -> bool:
    """Whether FinBERT can be loaded. The rules work without it."""
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


def _finbert_intensity(title: str) -> tuple[float, dict] | None:
    """How charged the wording is, 0..1. Deliberately direction-blind.

    Returns the combined positive+negative mass: a headline FinBERT calls
    strongly negative and one it calls strongly positive are equally *loud*,
    and loudness is all we take from it.
    """
    if not settings.finbert_enabled or not is_available():
        return None
    try:
        import torch

        model, tokenizer = _load()
        with torch.no_grad():
            logits = model(**tokenizer(title, return_tensors="pt", truncation=True)).logits
        probs = torch.softmax(logits, dim=-1)[0].tolist()
    except Exception:
        log.exception("FinBERT scoring failed; falling back to rules only")
        return None

    scores = {_FINBERT_LABELS[i]: p for i, p in enumerate(probs)}
    intensity = scores["positive"] + scores["negative"]
    return intensity, {k: round(v, 4) for k, v in scores.items()}


def score(headline) -> Score | None:
    """Score one relevant headline, or None if no event could be identified.

    None is deliberate rather than a neutral 0.0: a headline we could not read
    and one we read as genuinely balanced must stay distinguishable, or the
    index would average in placeholders as though they were real readings.
    """
    title = getattr(headline, "title", "") or ""
    event = events.classify(title)
    if event.direction == 0:
        return None

    intensity_result = _finbert_intensity(title)
    if intensity_result is None:
        # Rules alone are directionally sound but blunt about strength, so the
        # score is capped and the confidence says so.
        intensity, finbert_scores = 0.6, None
    else:
        intensity, finbert_scores = intensity_result

    magnitude = event.magnitude * event.entity_weight * (0.5 + 0.5 * intensity)
    value = round(event.direction * magnitude * 100, 1)

    confidence = round(
        min(event.entity_weight * (0.6 + 0.4 * intensity) * (0.6 if event.hedged else 1.0), 1.0),
        3,
    )

    return Score(
        value=value,
        direction=DIRECTION_NAME[event.direction],
        confidence=confidence,
        event=event.kind,
        components={
            "event": event.kind,
            "event_magnitude": round(event.magnitude, 3),
            "entity_weight": event.entity_weight,
            "intensity": round(intensity, 3),
            "hedged": event.hedged,
            "matched": event.matched,
            "finbert": finbert_scores,
        },
    )

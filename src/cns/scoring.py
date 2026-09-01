"""Score a headline as bullish or bearish for crude.

Three scorers, selected by ``SCORER_MODE``. Their scores are stored under
different versions (see `version()`), so switching modes adds a second opinion
rather than overwriting the first, and the three stay comparable on identical
headlines.

**hybrid** (recommended) -- takes direction from whichever reading of the
headline is structurally soundest, and uses FinBERT only for intensity. See
`_score_hybrid` for the precedence and `cns.stance` for the reasoning. Graded
against three days of hand-read live headlines (`tests/direction_labels`):

                            sentiment      hybrid
    directional accuracy    45/70  (64%)   69/70  (99%)
    sign flipped            14              0
    false signals           28/43  (65%)    9/43  (21%)
    weighted index error    43%             8%

    Read the 99% with the caveat that the stance lexicon was tuned against that
    same set, so it measures fit, not generalisation. The honest number is the
    older `scripts/eval_inversion.py` case list, written before this scorer
    existed and never tuned against: 18/18 there, versus 17/18 for sentiment.

**sentiment** -- FinBERT net sentiment, `P(positive) - P(negative)`,
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

from . import events, stance
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
    #: 0..1, how much this subject prices a barrel at all. Distinct from
    #: `confidence`, which is how sure we are of the direction: a village
    #: changing hands can be reported with certainty and still deserve no
    #: weight in an oil index. Consumed by `market_index.compute`.
    salience: float = 1.0


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

    With `SCORER_INVERT` on, the sign is flipped -- but only for headlines that
    are *not* direct market reports.

    Inverting is right when something is happening *to* supply: outages,
    sanctions, blocked tankers, war. Grim news there is bullish for crude. It is
    exactly wrong when the headline already reports oil's own numbers moving,
    where tone and price agree -- "Brent tumbles below $60" is negative and
    bearish. `events.describes_market_directly` makes that call.

    Measured over 18 headlines with unambiguous direction: plain FinBERT 6/18,
    blanket inversion 11/18, routed inversion 17/18.
    """
    probs = _finbert_probs(title)
    if probs is None:
        log.warning("sentiment mode needs FinBERT; install the ml extra")
        return None

    net = probs["positive"] - probs["negative"]
    # Route rather than flip blindly: inverting is right for events acting on
    # supply, and wrong where the headline already reports oil moving.
    direct = events.describes_market_directly(title)
    inverted = settings.scorer_invert and not direct
    if inverted:
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
            "inverted": inverted,
            "describes_market_directly": direct,
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


#: Salience below which we decline to call a direction at all.
#:
#: Not a confidence cut -- a subject cut. "27 killed in Bucha" can be reported
#: with total certainty and still says nothing about a barrel, and the index has
#: no way to recover from a stream of confident readings on headlines that carry
#: no reading. Set at 0.30 because `stance.salience` puts casualty and
#: territorial reports at 0.15 and unattributable non-energy news at 0.16.
_SALIENCE_FLOOR = 0.30

#: A move of this size is a full-scale price reading. Brent moving 3% in a
#: session is a big day; the -96.4 the tone scorer returned for a 0.43% drift
#: left no room to say so.
_FULL_SCALE_MOVE = 0.03



def _resolve_direction(title, probs, net, intensity, base, hedge_factor):
    """Best available reading of the headline, ignoring how much it matters.

    Returns ``(direction, source, magnitude, confidence, extra)``. Direction is
    never 0 unless the model genuinely returned a dead heat, because the whole
    point of the index is to have a call on every headline. How much that call
    should *count* is a separate question, answered by salience and confidence
    in `_score_hybrid`.

    The precedence is the point. Each step is a structurally sounder source of
    direction than the one after it, so the first that applies wins and tone is
    what is left when nothing else fits.
    """
    if stance.negated_disruption(title):
        return -1, "negated_disruption", base * 0.7, 0.5, {}

    event = events.classify(title)
    if event.kind not in (events.UNKNOWN, events.RISK_UP, events.RISK_DOWN):
        magnitude = min(event.magnitude * (0.5 + 0.5 * intensity), 1.0)
        return (event.direction, f"event:{event.kind}", magnitude,
                event.entity_weight * (0.6 + 0.4 * intensity) * hedge_factor,
                {"event": event.kind, "matched": event.matched})

    polarity = stance.polarity(title)
    if polarity != stance.NONE:
        direction = 1 if polarity == stance.COERCION else -1
        return (direction, f"stance:{polarity}", base,
                (0.5 + 0.5 * intensity) * hedge_factor, {})

    if events.describes_market_directly(title):
        # Size the call to the move the headline states, not to how bleak the
        # sentence sounds. An unsized move keeps the tone magnitude.
        moved = events.stated_move_fraction(title)
        magnitude = base if moved is None else min(moved / _FULL_SCALE_MOVE, 1.0)
        confidence = (1.0 - probs["neutral"]) if probs else 0.3
        return (_sign(net), "market_direct", magnitude, confidence,
                {"net": round(net, 4), "stated_move": moved})

    # Nothing structural applies: fall back to the inverted-tone reading, at
    # reduced confidence because it is the reading we trust least. Magnitude
    # tracks |net| directly, so a headline FinBERT finds balanced gets a call
    # with a number near zero rather than no call at all.
    confidence = (1.0 - probs["neutral"]) * 0.8 if probs else 0.2
    return (_sign(-net), "tone_inverted", min(abs(net), 1.0) * hedge_factor,
            confidence, {"net": round(net, 4), "inverted": True})


def _sign(value: float) -> int:
    """Direction of a net sentiment, breaking an exact tie as bearish.

    A tie is vanishingly rare and carries a magnitude near zero either way, so
    which side it falls on costs nothing -- but returning 0 would reintroduce
    the neutral label this scorer exists to avoid.
    """
    return 1 if value > 0 else -1


def _score_hybrid(title: str) -> Score | None:
    """A directional call on every headline, weighted by how much it matters.

    Direction and weight are answered separately, which is the change that
    makes both answerable honestly:

    * `_resolve_direction` always returns a side. "27 killed in Bucha" is read
      as bullish, because that is what the only available evidence says.
    * `salience` and `confidence` say how much to believe it. That headline
      lands at salience 0.15 and a magnitude near zero, so `market_index`
      weights it at roughly a fortieth of a Hormuz closure.

    An earlier version returned `neutral` for the low-salience cases. That was
    wrong for this project: the feed is a trading signal, and "no opinion" is
    not a usable one. The information that the headline is weak now lives in
    the weights, where the index can use it, instead of in a label that erases
    the call.
    """
    salience = stance.salience(title)
    probs = _finbert_probs(title)
    if probs is None:
        intensity, net = 0.6, 0.0
    else:
        intensity = probs["positive"] + probs["negative"]
        net = probs["positive"] - probs["negative"]

    hedged = bool(events._HEDGE.search(title))
    hedge_factor = 0.6 if hedged else 1.0
    # A charged headline gets a bigger number than a flat one, but the floor
    # keeps a correctly-identified event from vanishing because the wording
    # happened to be dry.
    base = (0.45 + 0.45 * intensity) * hedge_factor

    direction, source, magnitude, confidence, extra = _resolve_direction(
        title, probs, net, intensity, base, hedge_factor
    )

    # Gates no longer suppress the call -- they damp it. A settlement print and
    # a casualty count still get a side; they just stop being able to move the
    # index the way a Hormuz closure does.
    damp, gate = 1.0, None
    if salience < _SALIENCE_FLOOR:
        damp, gate = 0.15, "off_topic"
    elif events.is_level_report(title):
        damp, gate = 0.2, "level_report"

    return Score(
        value=round(direction * magnitude * damp * 100, 1),
        direction=DIRECTION_NAME[direction],
        confidence=round(min(max(confidence * salience * damp, 0.0), 1.0), 3),
        event=source,
        components={
            "mode": "hybrid",
            "source": source,
            "gate": gate,
            "salience": round(salience, 3),
            "intensity": round(intensity, 3),
            "hedged": hedged,
            "finbert": {k: round(v, 4) for k, v in probs.items()} if probs else None,
            **extra,
        },
        salience=round(salience, 3),
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
    if settings.scorer_mode == "hybrid":
        return _score_hybrid(title)
    return _score_sentiment(title)

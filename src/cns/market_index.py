"""Cumulative bull/bear market index over a trailing window of scored headlines.

Design notes, because the obvious implementation is wrong in three ways:

1. **Weighted mean, not sum.** Summing scores makes the index track *news
   volume* rather than sentiment: a quiet bullish week would score lower than a
   noisy, evenly-split one. Dividing by total weight keeps the index in
   [-100, +100] and comparable across weeks of different activity.

2. **Time decay.** A headline from six days ago should not count like one from
   an hour ago. Weight halves every `half_life_hours`.

3. **A single number hides the two states that matter most.** An index near 0
   can mean "nothing happened" or "the market is violently split". So the index
   is always reported alongside `volume`, `effective_n` and `dispersion`.
   Read the index only in the context of those three.

`novelty` and `salience` weights are supplied by the scorer, not computed here.
Story-clustering belongs to the scoring stage; this module only consumes it.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Headline, HeadlineScore, IndexSnapshot, utcnow

DEFAULT_WINDOW_DAYS = 7
DEFAULT_HALF_LIFE_HOURS = 24.0

#: Index thresholds for the human-readable label.
_BANDS = (
    (40.0, "strongly bullish"),
    (15.0, "bullish"),
    (-15.0, "neutral"),
    (-40.0, "bearish"),
)


@dataclass
class MarketIndex:
    index_value: float
    label: str
    volume: int
    effective_n: float
    dispersion: float
    bull_share: float
    bear_share: float
    window_days: int
    half_life_hours: float
    scorer_version: str
    category: str
    computed_at: datetime
    window_start: datetime
    #: Standard deviations from the trailing baseline of past index values.
    #: None until enough snapshots exist -- the index is uncalibrated before that.
    zscore: float | None = None

    def as_dict(self) -> dict:
        out = asdict(self)
        for key in ("computed_at", "window_start"):
            out[key] = out[key].isoformat()
        return out


def _label(value: float) -> str:
    for threshold, name in _BANDS:
        if value >= threshold:
            return name
    return "strongly bearish"


def _decay(age_hours: float, half_life_hours: float) -> float:
    if half_life_hours <= 0:
        return 1.0
    return 0.5 ** (max(age_hours, 0.0) / half_life_hours)


def _clamp01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def compute(
    session: Session,
    *,
    scorer_version: str,
    category: str = "oil_direct",
    window_days: int = DEFAULT_WINDOW_DAYS,
    half_life_hours: float = DEFAULT_HALF_LIFE_HOURS,
    now: datetime | None = None,
) -> MarketIndex:
    now = now or utcnow()
    window_start = now - timedelta(days=window_days)

    rows = session.execute(
        select(HeadlineScore.score, HeadlineScore.confidence, HeadlineScore.novelty,
               HeadlineScore.salience, Headline.published_at)
        .join(Headline, Headline.id == HeadlineScore.headline_id)
        .where(
            HeadlineScore.scorer_version == scorer_version,
            HeadlineScore.category == category,
            Headline.published_at.is_not(None),
            Headline.published_at >= window_start,
            Headline.published_at <= now,
        )
    ).all()

    weighted: list[tuple[float, float]] = []
    for score, confidence, novelty, salience, published_at in rows:
        age_hours = (now - published_at).total_seconds() / 3600.0
        weight = (
            _decay(age_hours, half_life_hours)
            * _clamp01(confidence)
            * _clamp01(novelty)
            * max(salience, 0.0)
        )
        if weight > 0:
            weighted.append((float(score), weight))

    base = dict(
        volume=len(rows),
        window_days=window_days,
        half_life_hours=half_life_hours,
        scorer_version=scorer_version,
        category=category,
        computed_at=now,
        window_start=window_start,
    )

    if not weighted:
        return MarketIndex(
            index_value=0.0, label="no data", effective_n=0.0, dispersion=0.0,
            bull_share=0.0, bear_share=0.0, **base
        )

    total_weight = sum(w for _, w in weighted)
    index_value = sum(s * w for s, w in weighted) / total_weight

    # Kish effective sample size: how many headlines are really driving this.
    # Collapses toward 1 when a single recent headline dominates the weights.
    sum_w_sq = sum(w * w for _, w in weighted)
    effective_n = (total_weight ** 2) / sum_w_sq if sum_w_sq else 0.0

    variance = sum(w * (s - index_value) ** 2 for s, w in weighted) / total_weight
    dispersion = math.sqrt(variance)

    bull_weight = sum(w for s, w in weighted if s > 0)
    bear_weight = sum(w for s, w in weighted if s < 0)

    return MarketIndex(
        index_value=round(index_value, 2),
        label=_label(index_value),
        effective_n=round(effective_n, 2),
        dispersion=round(dispersion, 2),
        bull_share=round(bull_weight / total_weight, 4),
        bear_share=round(bear_weight / total_weight, 4),
        zscore=_zscore(session, index_value, scorer_version, category, window_days, now),
        **base,
    )


def _zscore(
    session: Session,
    value: float,
    scorer_version: str,
    category: str,
    window_days: int,
    now: datetime,
    *,
    baseline_days: int = 90,
    min_samples: int = 30,
) -> float | None:
    """Where this index sits against its own history.

    The raw index has no natural scale -- +18 means nothing without knowing the
    usual range. Returns None until `min_samples` snapshots exist, rather than
    inventing a number from a handful of points.
    """
    history = list(
        session.scalars(
            select(IndexSnapshot.index_value).where(
                IndexSnapshot.scorer_version == scorer_version,
                IndexSnapshot.category == category,
                IndexSnapshot.window_days == window_days,
                IndexSnapshot.captured_at >= now - timedelta(days=baseline_days),
            )
        )
    )
    if len(history) < min_samples:
        return None
    mean = sum(history) / len(history)
    var = sum((h - mean) ** 2 for h in history) / len(history)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return round((value - mean) / sd, 2)


def snapshot(session: Session, index: MarketIndex) -> IndexSnapshot:
    row = IndexSnapshot(
        captured_at=index.computed_at,
        scorer_version=index.scorer_version,
        category=index.category,
        window_days=index.window_days,
        half_life_hours=index.half_life_hours,
        index_value=index.index_value,
        volume=index.volume,
        effective_n=index.effective_n,
        dispersion=index.dispersion,
        bull_share=index.bull_share,
        bear_share=index.bear_share,
    )
    session.add(row)
    session.commit()
    return row

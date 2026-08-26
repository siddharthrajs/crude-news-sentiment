"""Scoring seam. CrudeBERT lands here.

Sits between the relevance filter and Teams delivery so the pipeline order is
fixed now and stage 3 drops in without moving anything: the poller already calls
`score()` for every relevant headline and passes the result to the notifier.

Returns None today, which the notifier renders as null score fields.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    """Whether a real scorer is wired up. False until CrudeBERT lands."""
    return False


def score(headline) -> Score | None:
    """Score one relevant headline. None means "not scored yet".

    Deliberately not a neutral 0.0: a headline nothing has scored and a headline
    scored as genuinely neutral must stay distinguishable, or the index would
    silently average in placeholders.
    """
    return None

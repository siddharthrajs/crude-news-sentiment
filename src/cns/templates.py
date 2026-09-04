"""Recurring structured prints from the feed, read as data rather than as prose.

A handful of the feed's headlines are the same sentence every day with different
numbers in it. They are the most reliable signal in the stream -- somebody has
already done the measurement -- and they were the worst-scored, because a
sentence full of numbers reads as flat prose and ended up on the tone fallback,
where the sign turned on incidental wording.

The three-day Hormuz transit series is the case that motivated this module:

    09-02  "Four commodity vessels pass Strait of Hormuz on Tuesday,
            compared with 10-day average of around 13"        BEARISH  -86.7
    09-03  "Six commodity ships pass Strait of Hormuz on Wednesday,
            below 10-day average of about 13"                 BULLISH  +89.0
    09-04  "Four commodity ships pass Strait of Hormuz on Thursday,
            below 10-day average of about 15"                 BULLISH  +85.5

Same measurement three days running -- chokepoint traffic at roughly a third of
normal, which is about as bullish as a single headline gets -- and the sign
turned on whether the sentence happened to say "below" or "compared with". Two
of three right by luck.

Parsing the two numbers instead removes the guess entirely: 4 against a 13
average is a 69% shortfall, and that is the whole reading. No lexicon, no model.

Templates sit *first* in `scoring._resolve_direction`, ahead of the event and
stance layers, because a number the feed states is better evidence than any rule
inferring one. Each template is deliberately narrow: it must find every number
it needs or it declines and lets the headline fall through to the normal path.
Half-matching a template is worse than not matching it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Counts in these headlines are written as words about as often as digits.
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}

_NUMBER = r"(?:\d+|" + "|".join(_WORD_NUMBERS) + r")"


def _number(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _WORD_NUMBERS.get(token)


@dataclass(frozen=True)
class Template:
    """A reading parsed straight out of a structured headline."""

    #: 1 bullish, -1 bearish. Never 0 -- a template that cannot call a side
    #: returns None instead, so the headline falls through.
    direction: int
    #: 0..1, from the size of the deviation the print states.
    magnitude: float
    #: 0..1. High by construction: these are measurements, not readings.
    confidence: float
    #: Identifies which template fired, for the audit trail.
    kind: str
    #: The parsed numbers, so a call can be checked after the fact.
    extra: dict


#: "Four commodity vessels pass Strait of Hormuz on Tuesday", "Six commodity
#: ships pass ...". Requires "commodity", which is what separates the daily
#: data print from someone talking about ships in a speech -- "Trump suggests
#: 22 ships passed through Hormuz last night" is not this.
_TRANSIT_COUNT = re.compile(
    rf"(?P<count>{_NUMBER})\s+commodity\s+(?:ship|vessel)s?\s+pass",
    re.I,
)

#: "10-day average of 15", "10-day average of about 13", "... of around 13".
_TRANSIT_AVERAGE = re.compile(
    rf"\d+[-\s]day\s+average\s+of\s+(?:about|around|roughly|some\s+)?\s*(?P<avg>{_NUMBER})",
    re.I,
)

_HORMUZ = re.compile(r"strait\s+of\s+hormuz", re.I)

#: Traffic never reads as exactly average, and a magnitude of 0 would render as
#: an empty meter on the Teams card -- which is what an *unscored* headline
#: looks like. Floor it so a matched template always shows as a call.
_MIN_MAGNITUDE = 0.02

#: These are counted transits, not inferred ones. The remaining doubt is about
#: what the number means for crude, not about the number.
_TRANSIT_CONFIDENCE = 0.9


def _hormuz_transit(title: str) -> Template | None:
    """Chokepoint traffic against its own trailing average.

    Direction is not a judgement call: fewer ships through Hormuz than normal is
    supply at risk and bullish, more is the disruption easing and bearish. The
    shortfall doubles as the magnitude, so traffic at a third of average reads
    near full scale and traffic a shade under average barely registers.
    """
    if not _HORMUZ.search(title):
        return None
    count_match = _TRANSIT_COUNT.search(title)
    average_match = _TRANSIT_AVERAGE.search(title)
    if not (count_match and average_match):
        return None

    count = _number(count_match.group("count"))
    average = _number(average_match.group("avg"))
    if count is None or not average:
        return None

    shortfall = (average - count) / average
    return Template(
        direction=1 if shortfall >= 0 else -1,
        magnitude=min(max(abs(shortfall), _MIN_MAGNITUDE), 1.0),
        confidence=_TRANSIT_CONFIDENCE,
        kind="hormuz_transit",
        extra={"count": count, "average": average, "shortfall": round(shortfall, 3)},
    )


#: Tried in order; first match wins. Order only matters if two ever overlap.
_TEMPLATES = (_hormuz_transit,)


def classify(title: str) -> Template | None:
    """The structured reading of this headline, or None if it is not one."""
    text = (title or "").strip()
    if not text:
        return None
    for template in _TEMPLATES:
        result = template(text)
        if result is not None:
            return result
    return None

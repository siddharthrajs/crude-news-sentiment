"""Classify a headline into the supply/demand event that moves the crude price.

This is the part general sentiment models get wrong. "OPEC announces deep cuts"
is negative in tone and *bullish* for crude, because a supply cut lifts price.
Measured on the live corpus, FinBERT calls that headline negative at 0.85 and
"OPEC raises production quotas" positive at 0.69 -- inverted in both directions.

So direction comes from the event, never from tone:

    supply down  -> bullish       demand up   -> bullish
    supply up    -> bearish       demand down -> bearish

Inventories are the confusing case and are handled explicitly: a *build* means
more oil sitting unused, which is bearish, even though "rising" sounds positive.

Magnitude is separate from direction. It comes from how big the event is
(entity weight x intensity words x extracted quantities), not from how strongly
a model feels about the wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SUPPLY_UP = "supply_up"
SUPPLY_DOWN = "supply_down"
DEMAND_UP = "demand_up"
DEMAND_DOWN = "demand_down"
RISK_UP = "risk_up"
RISK_DOWN = "risk_down"
UNKNOWN = "unknown"

#: Which way each event pushes the crude price.
DIRECTION = {
    SUPPLY_UP: -1,
    SUPPLY_DOWN: +1,
    DEMAND_UP: +1,
    DEMAND_DOWN: -1,
    RISK_UP: +1,     # threat to supply, priced as a premium
    RISK_DOWN: -1,   # threat receding, premium unwinds
    UNKNOWN: 0,
}


def _p(*terms: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)(?:" + "|".join(terms) + r")(?!\w)", re.I)


# --- vocabulary -----------------------------------------------------------

_SUPPLY_NOUN = _p(
    r"outputs?", r"productions?", r"quotas?", r"exports?", r"supply", r"supplies",
    r"shipments?", r"cargo(?:es)?", r"loadings?", r"flows?", r"capacity",
    r"refin\w+", r"pipelines?", r"wells?", r"rigs?", r"barrels?", r"bbls?",
)
_INVENTORY_NOUN = _p(
    r"inventor\w+", r"stocks?", r"stockpiles?", r"reserves?", r"storage",
    r"stock change", r"crude stocks?",
)
_DEMAND_NOUN = _p(
    r"demand", r"consumption", r"imports?", r"purchases?", r"buying",
    r"appetite", r"runs?", r"throughput",
)

_DECREASE = _p(
    r"cuts?", r"cutting", r"slash\w*", r"reduc\w+", r"lower\w*", r"curb\w*",
    r"halt\w*", r"suspend\w*", r"stop\w*", r"shut\w*", r"disrupt\w+",
    r"outages?", r"declin\w+", r"falls?", r"fell", r"drops?", r"dropped",
    r"plunge\w*", r"tumbl\w+", r"loss\w*", r"cancel\w+", r"ban\w*",
    r"blocks?", r"blocked", r"restrict\w+", r"draw\w*", r"deficits?", r"tighten\w*",
    r"collaps\w+", r"plummet\w*", r"slump\w*", r"crater\w*", r"sink\w*", r"sank",
    r"weaken\w*", r"soften\w*", r"trim\w*", r"pare\w*", r"scale back", r"taper\w*",
    r"idle\w*", r"offline", r"force majeure", r"embargo\w*", r"freez\w+",
)
_INCREASE = _p(
    r"rais\w+", r"rise[sn]?", r"rising", r"rose", r"increas\w+", r"boost\w*",
    r"hik\w+", r"expand\w+", r"ramp\w*", r"surg\w+", r"jump\w*", r"climb\w+",
    r"lift\w*", r"add\w*", r"builds?", r"building", r"glut", r"surplus\w*",
    r"flood\w*", r"record high", r"more than expected", r"restart\w*", r"resum\w+",
)

#: Geopolitical threat to supply. Bullish without touching a supply noun.
_RISK_UP = _p(
    r"attacks?", r"attacked", r"strikes?", r"struck", r"missiles?", r"drones?",
    r"war", r"invasion", r"seiz\w+", r"blockades?", r"sanction\w*", r"embargo\w*",
    r"escalat\w+", r"threat\w*", r"conflict", r"militants?", r"airstrikes?",
    r"clos\w+ the strait", r"shut\w* the strait",
)
#: Threat receding -- the risk premium unwinds.
_RISK_DOWN = _p(
    r"ceasefire", r"truce", r"peace deal", r"de-?escalat\w+", r"resum\w+ talks",
    r"lift\w* sanctions", r"eas\w+ sanctions", r"agreements?", r"agreed?", r"agrees",
    r"deal reached",
    r"functioning", r"normal", r"reopen\w*", r"restor\w+",
)

#: Entities whose decisions move the whole market, versus ones that do not.
_ENTITY_WEIGHT = (
    (_p(r"opec\+?", r"saudi\w*", r"aramco", r"riyadh"), 1.0),
    (_p(r"russia\w*", r"iran\w*", r"hormuz", r"strait"), 0.9),
    (_p(r"eia", r"api", r"cushing", r"spr", r"strategic petroleum reserve"), 0.8),
    (_p(r"iraq\w*", r"uae", r"kuwait", r"venezuela\w*", r"libya\w*", r"nigeria\w*"), 0.7),
    (_p(r"kazakh\w*", r"angola\w*", r"algeria\w*", r"oman\w*", r"qatar\w*"), 0.5),
)

_INTENSIFIER = _p(
    r"deep\w*", r"sharp\w*", r"massive\w*", r"huge", r"record", r"unprecedented",
    r"drastic\w*", r"steep\w*", r"major", r"significant\w*", r"substantial\w*",
    r"all[- ]time",
    # Verbs that are themselves emphatic -- "slashes" is not a mild cut.
    r"slash\w*", r"collaps\w+", r"plunge\w*", r"plummet\w*", r"flood\w*",
    r"halt\w*", r"shut\w*", r"surg\w+", r"crater\w*", r"glut",
)
_HEDGE = _p(
    r"may", r"might", r"could", r"considering", r"weigh\w+", r"mull\w+",
    r"proposal", r"proposed", r"talks", r"discuss\w+", r"expected", r"forecast\w*",
    r"denies", r"denied", r"unlikely", r"rumou?r\w*", r"reportedly", r"suggests?",
)

#: "2 million bpd", "4 mln bbls", "-3.2M". Spelled-out numerals appear often
#: enough in wire copy ("two million barrels") to be worth handling.
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "half": 0.5,
}
_QUANTITY = re.compile(
    r"(\d+(?:\.\d+)?|" + "|".join(_WORD_NUMBERS) + r")\s*"
    r"(million|mln|m|bln|billion|thousand|k)?\s*"
    r"(?:barrels?|bbls?|bpd|b/d|barrels? per day)",
    re.I,
)


@dataclass(frozen=True)
class Event:
    kind: str
    #: -1 bearish, 0 unknown, +1 bullish.
    direction: int
    #: 0..1 before confidence weighting.
    magnitude: float
    entity_weight: float
    hedged: bool
    matched: list[str]


def _entity_weight(text: str) -> float:
    for pattern, weight in _ENTITY_WEIGHT:
        if pattern.search(text):
            return weight
    return 0.4  # a named event we cannot attribute to a major player


def _quantity_magnitude(text: str) -> float:
    """Bigger stated volumes mean a bigger move. Absent numbers score neutral."""
    best = 0.0
    for amount, unit in _QUANTITY.findall(text):
        key = amount.lower()
        value = float(_WORD_NUMBERS[key]) if key in _WORD_NUMBERS else float(amount)
        unit = (unit or "").lower()
        if unit in ("million", "mln", "m"):
            value *= 1e6
        elif unit in ("bln", "billion"):
            value *= 1e9
        elif unit in ("thousand", "k"):
            value *= 1e3
        # 1m bbl is a routine cargo; 5m+ is a market-moving number.
        best = max(best, min(value / 5e6, 1.0))
    return best


def classify(title: str) -> Event:
    """Identify the supply/demand event a headline describes."""
    text = (title or "").strip()
    if not text:
        return Event(UNKNOWN, 0, 0.0, 0.0, False, [])

    decrease = bool(_DECREASE.search(text))
    increase = bool(_INCREASE.search(text))
    matched: list[str] = []
    kind = UNKNOWN

    # Inventories first: a build is bearish even though "rising" sounds good,
    # and a draw is bullish. Reading them as plain supply gets both backwards.
    if _INVENTORY_NOUN.search(text) and (increase or decrease):
        kind = SUPPLY_UP if increase else SUPPLY_DOWN
        matched.append("inventory_build" if increase else "inventory_draw")
    elif _DEMAND_NOUN.search(text) and (increase or decrease):
        kind = DEMAND_UP if increase else DEMAND_DOWN
        matched.append("demand")
    elif _SUPPLY_NOUN.search(text) and (increase or decrease):
        kind = SUPPLY_DOWN if decrease else SUPPLY_UP
        matched.append("supply")
    elif _RISK_DOWN.search(text):
        kind = RISK_DOWN
        matched.append("risk_easing")
    elif _RISK_UP.search(text):
        kind = RISK_UP
        matched.append("risk")

    if kind == UNKNOWN:
        return Event(UNKNOWN, 0, 0.0, _entity_weight(text), False, [])

    entity = _entity_weight(text)
    # Inventory reports move the whole market regardless of who is named, so
    # they should not fall back to the unattributed default.
    if any(m.startswith("inventory") for m in matched):
        entity = max(entity, 0.8)
    hedged = bool(_HEDGE.search(text))

    # Base magnitude, lifted by intensity words and any stated volume.
    magnitude = 0.5
    if _INTENSIFIER.search(text):
        magnitude += 0.25
        matched.append("intensifier")
    quantity = _quantity_magnitude(text)
    if quantity:
        magnitude += 0.25 * quantity
        matched.append("quantity")
    # A proposal is not a decision.
    if hedged:
        magnitude *= 0.5
        matched.append("hedged")

    return Event(
        kind=kind,
        direction=DIRECTION[kind],
        magnitude=min(magnitude, 1.0),
        entity_weight=entity,
        hedged=hedged,
        matched=matched,
    )


#: Words that mark a headline as reporting the oil market's own numbers rather
#: than an event acting on them: a price level, a percentage move, a settlement.
_PRICE_MARKER = _p(
    r"prices?", r"futures", r"a barrel", r"per barrel", r"settle\w*", r"session",
    r"benchmark", r"\$\d[\d.,]*", r"\d[\d.,]*%",
)
_PRICE_SUBJECT = _p(r"oil", r"crude", r"brent", r"wti", r"prices?", r"futures")

#: Verbs for a price moving. Kept separate from the supply vocabulary: "eases"
#: means a smaller price here but a *larger* supply in "eases sanctions", so
#: mixing the two lists would corrupt event classification.
_PRICE_MOVE = _p(
    r"rall(?:y|ies|ied)", r"slid\w*", r"slides?", r"slip\w*", r"dip\w*",
    r"tumbl\w+", r"plunge\w*", r"surg\w+", r"jump\w*", r"climb\w+",
    r"falls?", r"fell", r"rise[sn]?", r"rose", r"drops?", r"dropped",
    r"gains?", r"gained", r"lose[sn]?", r"lost", r"eas\w+", r"retreat\w*",
    r"advance\w*", r"settle\w*", r"weaker", r"stronger", r"higher", r"lower",
)

#: Supply nouns that identify an event acting on the market. Excludes barrels,
#: which in "below $60 a barrel" is a price unit, not a supply story.
_SUPPLY_EVENT_NOUN = _p(
    r"outputs?", r"productions?", r"quotas?", r"exports?", r"supply", r"supplies",
    r"shipments?", r"cargo(?:es)?", r"loadings?", r"capacity",
    r"refin\w+", r"pipelines?", r"wells?", r"rigs?",
)


def describes_market_directly(title: str) -> bool:
    """Whether the headline reports oil's own numbers moving.

    This is the routing question for sentiment inversion. Inverting FinBERT is
    right when a headline describes something *happening to* supply -- an
    outage, a sanction, a war -- because grim news is bullish for crude. It is
    exactly wrong when the headline already reports the market's own direction,
    where tone and price agree:

        "Brent crude tumbles below $60 a barrel"   negative, and bearish
        "Global oil demand collapses"              negative, and bearish
        "US crude inventories post a huge build"   bearish

    Measured over 18 unambiguous headlines, inverting scored 11/12 on the first
    kind and 0/6 on the second. Routing on this distinction is worth more than
    any change to the model.
    """
    text = (title or "").strip()
    if not text:
        return False

    moved = bool(_INCREASE.search(text) or _DECREASE.search(text))

    # Inventories and demand are the market's own quantities.
    if moved and (_INVENTORY_NOUN.search(text) or _DEMAND_NOUN.search(text)):
        return True

    # A price report needs a price subject, a price marker and a move -- and no
    # supply event to attribute it to. "OPEC raises production quotas by 5%"
    # carries a percentage but is a supply decision, and inverting it is right.
    if _SUPPLY_EVENT_NOUN.search(text):
        return False
    return bool(
        _PRICE_SUBJECT.search(text)
        and _PRICE_MARKER.search(text)
        and (_PRICE_MOVE.search(text) or moved)
    )

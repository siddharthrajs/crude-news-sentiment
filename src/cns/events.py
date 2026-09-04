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

#: The strategic reserve, which behaves as supply on the way out and as demand
#: on the way in. Nothing else in the inventory vocabulary is asymmetric.
_SPR = _p(
    r"spr", r"strategic petroleum reserves?", r"strategic reserves?",
    r"strategic national reserves?", r"strategic stockpiles?",
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
    r"replenish\w*", r"refill\w*", r"top\w* up", r"stockpil\w+",
    # Releasing from storage and tapping a reserve both put barrels on the
    # market, so they belong with the increases even though the words are
    # not obviously about growth.
    r"releas\w+", r"tapp?(?:s|ed|ing)?", r"draw\w* down", r"drawdowns?",
    # Capacity coming back is supply returning to the market. Needs a supply
    # noun alongside to fire, so "Iran returns to negotiations" is unaffected.
    r"returns?", r"returned", r"returning", r"back online", r"restor\w+",
    r"full capacity", r"normal levels?",
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

    # Which quantity is moving? A headline often names more than one --
    # "Russia's gasoline output fell to about 70% of domestic consumption"
    # carries both a supply noun and a demand noun -- so the earliest one wins.
    # In wire copy the subject leads, and taking them in a fixed order instead
    # read that headline as falling demand and inverted its sign at confidence
    # 0.98, when it is a supply loss and bullish.
    #
    # Inventories keep their tie-break priority: a build is bearish even though
    # "rising" sounds good, and a draw is bullish, so reading a stock report as
    # plain supply gets both backwards.
    if increase or decrease:
        # In a price report "barrel" is the unit, not the subject: "Brent crude
        # tumbles below $60 a barrel" is not a supply cut. `_SUPPLY_EVENT_NOUN`
        # is the same list with the price units removed, and switching to it
        # whenever a price marker is present keeps that headline out of the
        # supply branch entirely.
        supply_noun = (
            _SUPPLY_EVENT_NOUN if _PRICE_MARKER.search(text) else _SUPPLY_NOUN
        )
        subjects = []
        for noun, name in (
            (_INVENTORY_NOUN, "inventory"),
            (_DEMAND_NOUN, "demand"),
            (supply_noun, "supply"),
        ):
            found = noun.search(text)
            if found:
                subjects.append((found.start(), name))
        subject = min(subjects)[1] if subjects else None

        if subject == "inventory":
            # The SPR is the exception that proves the rule. A release adds
            # barrels to the market and is bearish, but a refill is the
            # government *buying* them, which is demand and bullish -- and
            # "replenish strategic reserves" was being read as a stock build.
            if _SPR.search(text) and increase:
                kind = DEMAND_UP
                matched.append("spr_refill")
            else:
                kind = SUPPLY_UP if increase else SUPPLY_DOWN
                matched.append("inventory_build" if increase else "inventory_draw")
        elif subject == "demand":
            kind = DEMAND_UP if increase else DEMAND_DOWN
            matched.append("demand")
        elif subject == "supply":
            kind = SUPPLY_DOWN if decrease else SUPPLY_UP
            matched.append("supply")

    if kind == UNKNOWN and _RISK_DOWN.search(text):
        kind = RISK_DOWN
        matched.append("risk_easing")
    elif kind == UNKNOWN and _RISK_UP.search(text):
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

    # A named supply event settles it before anything else is considered.
    # "Russia's gasoline output fell to about 70% of domestic consumption"
    # mentions consumption, but `output ... fell` is a supply loss and reading
    # it as a demand report inverted the sign at confidence 0.98. The supply
    # guard used to sit below the demand branch and never got the chance.
    if _SUPPLY_EVENT_NOUN.search(text):
        return False

    # Inventories and demand are the market's own quantities.
    if moved and (_INVENTORY_NOUN.search(text) or _DEMAND_NOUN.search(text)):
        return True

    # A price report needs a price subject, a price marker and a move.
    # "OPEC raises production quotas by 5%" carries a percentage but is a
    # supply decision, and is already excluded by the guard above.
    return bool(
        _PRICE_SUBJECT.search(text)
        and _PRICE_MARKER.search(text)
        and (_PRICE_MOVE.search(text) or moved)
    )


#: "down 39 cents, 0.43%", "rises 3.5%", "fell 2.46% year-over-year".
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")

#: Marks a headline as quoting a level or a forecast rather than reporting a
#: change to one: a settlement price, a survey average, an export volume.
_LEVEL_REPORT = _p(
    r"settles?", r"settled", r"settlement", r"averages?", r"averaged",
    r"forecast\w*", r"expected", r"poll", r"survey", r"estimates?", r"reached",
    r"stands? at", r"at about", r"unchanged", r"steady", r"flat",
    # An official selling price is a level by definition: the number is where
    # the producer set the price, not a move in it. All three of the Saudi OSPs
    # in the corpus were being caught here only by accident -- two matched on
    # incidental words in their *comparison* clause ("vs Oman/Dubai average",
    # "vs ICE Brent settlement") and the third, priced "vs ASCI", matched
    # nothing and was scored as prose at -34.3.
    r"OSPs?", r"official selling price",
)

#: The unit that makes the quoted figure a market level -- a price or a volume.
#: Wider than `_PRICE_MARKER`, which knows only about prices: "U.S. oil output
#: to average 13.83 million bpd in August vs 13.82 million bpd in July" is the
#: same kind of non-event and quotes barrels rather than dollars.
_LEVEL_UNIT = _p(
    r"\$\d[\d.,]*", r"a barrel", r"per barrel", r"/bbl", r"bbls?", r"barrels?",
    r"bpd", r"b/d", r"a gallon", r"per gallon", r"mmbtu", r"bcf", r"mcf",
    r"tonnes?", r"cents", r"prices?", r"futures",
)

#: Verbs that report an actual move, for disqualifying a level report. Narrower
#: than `_PRICE_MOVE`, which includes `settle` -- and a settlement *is* the
#: level, so reusing that list would disqualify every print we want to catch.
_DIRECTIONAL_MOVE = _p(
    r"rall(?:y|ies|ied)", r"slid\w*", r"slides?", r"slip\w*", r"dip\w*",
    r"tumbl\w+", r"plunge\w*", r"surg\w+", r"jump\w*", r"climb\w+",
    r"falls?", r"fell", r"rise[sn]?", r"rose", r"drops?", r"dropped",
    r"gains?", r"gained", r"lose[sn]?", r"lost", r"retreat\w*", r"advance\w*",
    r"up", r"down", r"higher", r"lower", r"weaker", r"stronger",
)


def stated_move_fraction(title: str) -> float | None:
    """The size of the move a headline states, as a fraction, or None.

    Magnitude has to come from the number, not the verb. "Brent settles down
    39 cents, 0.43%" and "Brent collapses 12%" are the same sentence shape and
    two entirely different days, but FinBERT reads both as strongly negative
    and scored the first at -96.4. Returning 0.0043 lets the scorer size the
    call to the move that actually happened.

    None means the headline states no percentage, which is not the same as
    stating zero -- the caller decides what to do with an unsized move.
    """
    text = (title or "").strip()
    percents = [float(m) for m in _PERCENT.findall(text)]
    if not percents:
        return None
    # Wire copy quotes the change last ("down 39 cents, 0.43%"); where several
    # appear, the smallest is the move and the larger ones are levels or shares.
    return min(percents) / 100.0


def is_level_report(title: str) -> bool:
    """Whether the headline quotes a level or forecast with no change to it.

    "NYMEX Diesel September futures settle at $4.3567 a gallon" carries no
    direction at all -- it is the print, not the move. Scored as tone it came
    back bearish at 0.78 confidence, purely because a table of numbers reads
    as unexciting prose.
    """
    text = (title or "").strip()
    if not text:
        return False
    # A stated move disqualifies it: this is about prints with nothing moving.
    if _PERCENT.search(text) or _DIRECTIONAL_MOVE.search(text):
        return False
    return bool(_LEVEL_REPORT.search(text) and _LEVEL_UNIT.search(text))

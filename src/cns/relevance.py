"""Decide whether a narrative headline is about crude oil or oil-relevant geopolitics.

Two categories survive; everything else is dropped:

* ``oil_direct`` -- the headline is about the physical or traded oil complex.
* ``geo_risk``   -- geopolitics with a credible path to the oil price.

The hard part is geopolitics, because naming an oil producer is not enough:
"Canada's PM Carney set to address the EU Parliament" mentions a top-five
producer and means nothing for crude. So actors are tiered:

* **Tier 1** are actors whose mere appearance in a financial newswire is
  oil-relevant -- Iran, OPEC, Saudi, Russia, Venezuela, Libya, and the shipping
  chokepoints. These pass on their own.
* **Tier 2** are states that matter for oil only when something is happening to
  them -- Ukraine, Israel, Iraq, Qatar and friends. These need a risk term
  (war, sanctions, strike, drone, ceasefire...) alongside.

Tuned against 79 hand-read headlines captured from the live feed. Deliberately
recall-biased: a missed headline is invisible, while a false positive is caught
downstream by the scorer's confidence weight and is auditable via
``relevance_terms``.
"""

from __future__ import annotations

import re

OIL_DIRECT = "oil_direct"
GEO_RISK = "geo_risk"
IRRELEVANT = "irrelevant"


def _alt(*terms: str) -> re.Pattern[str]:
    """Word-boundary alternation. Terms may contain spaces or regex fragments."""
    return re.compile(r"(?<!\w)(?:" + "|".join(terms) + r")(?!\w)", re.I)


#: The oil complex itself. Deliberately excludes the bare word "energy":
#: "civil nuclear energy with Saudi Arabia" is geopolitics, not an oil story.
OIL_TERMS = _alt(
    r"crude", r"oil", r"petroleum", r"brent", r"wti", r"opec\+?", r"aramco",
    r"bbls?", r"barrels?", r"refin(?:ery|eries|er|ers|ing)", r"pipelines?",
    r"tankers?", r"condensate", r"distillates?", r"gasoline", r"diesel",
    r"naphtha", r"gasoil", r"jet fuel", r"bunker fuel", r"fuel oil",
    r"cushing", r"spr", r"strategic petroleum reserve", r"rig counts?",
    r"shale", r"upstream", r"downstream", r"contango", r"backwardation",
    r"lng", r"natural gas", r"natgas", r"drilling", r"oilfield", r"petrochemical",
)

#: Pass on their own -- no risk term required.
TIER1_ACTORS = _alt(
    r"iran", r"irans", r"iranian", r"tehran", r"irgc", r"revolutionary guards?",
    r"opec\+?", r"saudi", r"saudis", r"riyadh", r"aramco",
    r"russia", r"russias", r"russian", r"moscow", r"rosneft", r"lukoil",
    r"venezuela", r"venezuelan", r"caracas", r"pdvsa",
    r"libya", r"libyan", r"houthis?", r"opec",
)

#: Shipping chokepoints and the regional catch-all. Always oil-relevant.
CHOKEPOINTS = _alt(
    r"hormuz", r"straits?", r"bab[- ]el[- ]mandeb", r"bab[- ]al[- ]mandab",
    r"suez", r"red sea", r"malacca", r"bosphorus", r"dardanelles",
    r"kerch", r"middle east", r"persian gulf", r"arabian gulf",
)

#: Strong enough on their own that the actor does not matter.
STANDALONE_RISK = _alt(
    r"sanctions?", r"sanctioned", r"embargo(?:es)?", r"oil price cap",
)

#: Matter for oil only when combined with a risk term.
TIER2_ACTORS = _alt(
    r"ukraine", r"ukraines", r"ukrainian", r"kyiv", r"kiev",
    r"israel", r"israeli", r"gaza", r"hamas", r"hezbollah", r"lebanon",
    r"iraq", r"iraqi", r"baghdad", r"kurdistan", r"syria", r"syrian",
    r"yemen", r"yemeni", r"qatar", r"doha", r"uae", r"abu dhabi", r"dubai",
    r"kuwait", r"oman", r"omani", r"muscat", r"bahrain",
    r"nigeria", r"nigerian", r"algeria", r"kazakhstan", r"angola", r"sudan",
)

RISK_TERMS = _alt(
    r"wars?", r"strikes?", r"struck", r"missiles?", r"drones?", r"attacks?",
    r"attacked", r"blockades?", r"seiz(?:e|ed|ure|ures)", r"ceasefire",
    r"truce", r"peace", r"conflicts?", r"militants?", r"nuclear",
    r"escalat\w*", r"retaliat\w*", r"invasion", r"invade[ds]?", r"troops",
    r"military", r"tensions?", r"prisoners?", r"hostilit\w+", r"airstrikes?",
    r"bomb(?:ed|ing|ings)?", r"shelling", r"insurgen\w+", r"coup",
    r"coalition forces", r"coast ?guard", r"navy", r"naval",
    # De-escalation is as much a signal as escalation -- "ceasefire" and
    # "peace" were already here, so the rest of the diplomacy vocabulary
    # belongs too. Bare "talks" also matches the verb ("Aoun talks to
    # reporters"), which is the recall-biased trade this filter is making.
    r"negotiat\w+", r"talks", r"diplomat\w*", r"mediat\w+", r"broker(?:ed|ing)",
)

#: Cap on how many matched terms are recorded, to keep the audit column small.
_MAX_TERMS = 8


def _hits(pattern: re.Pattern[str], text: str) -> list[str]:
    return [m.group(0).lower() for m in pattern.finditer(text)]


def classify(title: str) -> tuple[str, list[str]]:
    """Return ``(category, matched_terms)`` for a narrative headline."""
    text = (title or "").strip()
    if not text:
        return IRRELEVANT, []

    oil = _hits(OIL_TERMS, text)
    if oil:
        # Oil wins over geo when both match: it is the more direct signal.
        return OIL_DIRECT, sorted(set(oil))[:_MAX_TERMS]

    chokepoints = _hits(CHOKEPOINTS, text)
    tier1 = _hits(TIER1_ACTORS, text)
    standalone = _hits(STANDALONE_RISK, text)
    if chokepoints or tier1 or standalone:
        return GEO_RISK, sorted(set(chokepoints + tier1 + standalone))[:_MAX_TERMS]

    tier2 = _hits(TIER2_ACTORS, text)
    risk = _hits(RISK_TERMS, text)
    if tier2 and risk:
        return GEO_RISK, sorted(set(tier2 + risk))[:_MAX_TERMS]

    return IRRELEVANT, []


def is_relevant(title: str) -> bool:
    return classify(title)[0] != IRRELEVANT

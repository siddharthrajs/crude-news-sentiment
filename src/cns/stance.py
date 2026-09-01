"""Read a headline's *stance* rather than its tone.

`scoring._score_sentiment` asks FinBERT how the wording feels and flips the sign.
That works while tone and substance move together, and fails hard on the two
shapes this feed is mostly made of.

**Attributed quotes.** Roughly half the wire is `Speaker: claim`, and a speaker
picks their register for diplomatic reasons that have nothing to do with crude:

    "Bessent: We thank the EU for strong support of actions against Iran."

FinBERT scores that positive at 0.939 -- it is reading *thank* and *strong
support*, which is a courtesy, not a market event. Inverting a courtesy produces
a confident bearish call on what is actually an escalation of sanctions
pressure. Strip the courtesy and the same model reads the substance correctly:
"New sanctions imposed on Iran" comes back negative at 0.645, so bullish after
inversion. The tone is not wrong about the words; the words are about the
speaker's manners.

So for a headline whose content is coercion or diplomacy, direction comes from
`polarity()` -- an explicit two-sided lexicon of *pressure applied* against
*pressure released* -- and FinBERT is demoted to supplying intensity only. This
is the same argument `cns.events` makes for supply verbs, extended to the
geopolitical half of the corpus.

**Negation.** FinBERT barely registers it. Measured here:

    "Ships have hit mines in the Strait of Hormuz"      negative 0.905
    "No ships have hit mines in the Strait of Hormuz"   negative 0.514

The `No` moves the probability but never crosses to the other side, so after
inversion both come out bullish -- when the second is a CENTCOM reassurance
that the feared disruption did *not* happen, which unwinds risk premium and is
bearish. `negated_disruption()` catches that as a scoped rule instead: a
negation cue governing a disruption verb within a short window.

Both lexicons are deliberately narrow. A miss costs a headline scored neutral;
a false positive costs a confident call in the wrong direction, which is the
error `market_index` cannot recover from.
"""

from __future__ import annotations

import re

#: Pressure applied -- bullish, because it threatens barrels reaching market.
COERCION = "coercion"
#: Pressure released -- bearish, because the risk premium unwinds.
DEESCALATION = "deescalation"
#: Not a stance headline; the caller should fall through to another scorer.
NONE = "none"


def _p(*terms: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)(?:" + "|".join(terms) + r")(?!\w)", re.I)


# --- negation -------------------------------------------------------------

#: Cues that negate whatever follows them. `no`/`not`/`never` plus the
#: contracted and reported forms the wire actually uses.
_NEG_CUE = _p(
    r"no", r"not", r"n't", r"never", r"none", r"nothing", r"neither", r"nor",
    r"denies", r"denied", r"deny", r"rejects?", r"rejected",
    r"dismiss(?:es|ed)?", r"ruled out", r"rules out",
)

#: What a negation has to be governing for the headline to count as a
#: reassurance. Deliberately restricted to *disruption* words -- the physical
#: events whose absence is genuinely bearish.
#:
#: `sanctions` is excluded on purpose. It appears in a third of this corpus, and
#: "won't succeed unless Chinese firms face secondary sanctions" would otherwise
#: read as a negated sanction when it is the opposite: a demand for more of
#: them. Same reasoning excludes bare `target`, which "no target is beyond
#: Tehran's reach" would trip while meaning the reverse.
_DISRUPTION = _p(
    r"hit", r"struck", r"strikes?", r"attacks?", r"attacked", r"damaged?",
    r"mines?", r"mined", r"disrupt\w+", r"disruptions?", r"halt\w*", r"stop\w*",
    r"suspend\w*", r"blocked?", r"blockad\w+", r"clos\w+", r"shut\w*",
    r"outages?", r"spills?", r"fires?", r"explosions?", r"sunk", r"seiz\w+",
    r"affected", r"impacted", r"interrupt\w+", r"casualt\w+", r"threat\w*",
)

#: How far after a cue the disruption may sit and still be governed by it.
#: Six tokens spans "No ships have hit mines" and "operations have not stopped"
#: without reaching across a clause boundary into unrelated vocabulary.
_NEG_WINDOW = 6

#: "warned Israel against carrying out strikes" -- an action prevented rather
#: than negated, which lands in the same place: the disruption does not happen.
_PREVENTED = re.compile(
    r"(?<!\w)(?:warn(?:s|ed|ing)?|caution(?:s|ed)?|advis(?:es|ed))\b[^,;]{0,40}?"
    r"\bagainst\b",
    re.I,
)

_WORD = re.compile(r"[\w'’-]+")


def negated_disruption(title: str) -> bool:
    """Whether the headline says a feared disruption did *not* happen.

    This is a reassurance -- "No ships have hit mines", "operations have not
    stopped" -- and unwinds risk premium, so it is bearish, which is the exact
    opposite of what the tone reads as.
    """
    text = (title or "").strip()
    if not text:
        return False
    if _PREVENTED.search(text):
        return True

    tokens = [(m.group(0), m.start()) for m in _WORD.finditer(text)]
    for i, (token, start) in enumerate(tokens):
        # Match the cue against the token in isolation so "no" fires but
        # "Novoandriivka" does not.
        if not _NEG_CUE.fullmatch(token) and not (
            token.lower().endswith("n't") and _NEG_CUE.search(token)
        ):
            continue
        window = tokens[i + 1 : i + 1 + _NEG_WINDOW]
        if any(_DISRUPTION.fullmatch(word) for word, _ in window):
            return True
    return False


# --- stance ---------------------------------------------------------------

#: Pressure being applied to a producer or a shipping route. Bullish.
#:
#: The phrases matter as much as the single words: `actions against`,
#: `pressure on` and `measures against` are how a wire reports coercion when
#: the speaker is being polite about it, and they are exactly the headlines
#: tone gets backwards.
_COERCION = _p(
    r"sanction\w*", r"embargo\w*", r"blockad\w+", r"blacklist\w*",
    r"designat(?:e|es|ed|ion)", r"interdict\w*", r"seiz\w+", r"impound\w*",
    r"attacks?", r"attacked", r"strikes?", r"struck", r"airstrikes?",
    r"missiles?", r"rockets?", r"drones?", r"mines?", r"mine-laying",
    r"retaliat\w+", r"escalat\w+", r"war", r"invasion", r"hostilit\w+",
    r"militant\w*", r"aggression", r"repercussions?", r"punishment",
    r"crackdown", r"tariffs?", r"restrict\w+", r"curb\w*", r"ban\w*",
    r"pressure", r"pressuring", r"coerc\w+", r"threat\w*", r"warns?", r"warned",
    r"respond", r"response", r"resist\w*", r"defian\w+", r"violat\w+",
    r"illegal\w*", r"lashing out", r"shot down", r"intercept\w+",
    r"target(?:s|ed|ing)?", r"hit", r"projectiles?", r"explosions?", r"blasts?",
)
_COERCION_PHRASE = re.compile(
    r"(?<!\w)(?:"
    r"actions?\s+against|measures?\s+against|steps?\s+against|"
    r"move(?:s|d)?\s+against|pressure\s+(?:on|campaign)|"
    r"secondary\s+sanctions|maintain\s+pressure|exert\w*\s+pressure|"
    r"closed\s+to\s+(?:all\s+)?ships|waterway\s+closed|clos\w+\s+the\s+strait|"
    r"must\s+(?:follow|comply|coordinate)|without\s+coordination|"
    r"told\s+not\s+to|barred\s+from|cut\s+off"
    r")(?!\w)",
    re.I,
)

#: Pressure being released. Bearish -- the premium unwinds.
#:
#: `relief`, `waiver` and `negotiated` are here because the escalation lexicon
#: above matches `sanctions`, and "sanctions relief" must not read as coercion.
#: De-escalation is therefore checked first in `polarity()`.
_DEESCALATION = _p(
    r"ceasefire", r"truce", r"peace deal", r"peace talks", r"de-?escalat\w+",
    r"relief", r"waivers?", r"exempt\w+", r"reprieve", r"unwind\w*",
    r"negotiat\w+", r"talks", r"dialogue", r"mediat\w+", r"broker\w*",
    r"accord", r"agreement", r"agreed", r"agrees", r"pact", r"settlement",
    r"deals?",
    r"reopen\w*", r"restor\w+", r"resum\w+", r"normalis\w+", r"normaliz\w+",
    r"contained", r"cooperation", r"understanding", r"rapprochement",
    r"concession\w*", r"compromise", r"detente", r"goodwill",
)
_DEESCALATION_PHRASE = re.compile(
    r"(?<!\w)(?:"
    r"sanctions?\s+relief|lift\w*\s+(?:the\s+)?sanctions|eas\w+\s+sanctions|"
    r"negotiated\s+solution|seeks?\s+a\s+negotiated|"
    r"limited\s+and\s+contained|no\s+one'?s\s+interest|"
    r"release\s+of\s+funds|open\s+(?:the\s+)?route|"
    r"is\s+functioning|operating\s+normally|back\s+to\s+normal"
    r")(?!\w)",
    re.I,
)


#: Diplomacy failing. A headline can name a negotiation and be reporting its
#: breakdown, which is the opposite of de-escalation:
#:
#:     "The US is obstructing the negotiation process between Oman and Iran,
#:      which has caused it to be delayed"
#:
#: matched `negotiat` and scored bearish at 0.89 confidence when the talks are
#: the thing going wrong. This is the same trap as "sanctions relief" -- a word
#: from one lexicon sitting inside a sentence that means the other -- so it gets
#: the same treatment: checked before the lexicon it would otherwise fool.
_OBSTRUCTED = _p(
    r"obstruct\w+", r"block\w*", r"derail\w*", r"stall\w*", r"stalemate",
    r"delay\w*", r"deadlock\w*", r"impasse", r"collaps\w+", r"break\w*down",
    r"broke down", r"fail\w*", r"failure", r"suspend\w*", r"walk\w* out",
    r"walk\w* away", r"withdraw\w*", r"abandon\w*", r"refus\w+", r"reject\w+",
    r"scrap\w*", r"cancel\w+", r"no deal", r"without agreement", r"sabotag\w+",
)


def polarity(title: str) -> str:
    """Whether the headline applies pressure, releases it, or does neither.

    De-escalation is tested first: "sanctions relief" and "lift sanctions"
    contain a coercion word and mean its opposite, and that ordering is the
    whole reason both lexicons can stay simple. Obstruction is tested before
    *that*, for the same reason in the other direction.
    """
    text = (title or "").strip()
    if not text:
        return NONE
    deescalating = bool(_DEESCALATION_PHRASE.search(text) or _DEESCALATION.search(text))
    if deescalating and _OBSTRUCTED.search(text):
        # Talks named but going nowhere. Not a premium unwind, so fall through
        # to the coercion test rather than asserting the opposite outright --
        # "talks stalled" is bullish, but weakly, and only if something else in
        # the headline says so.
        return COERCION if _COERCION_PHRASE.search(text) or _COERCION.search(text) else NONE
    if deescalating:
        return DEESCALATION
    if _COERCION_PHRASE.search(text) or _COERCION.search(text):
        return COERCION
    return NONE


# --- salience -------------------------------------------------------------

#: Human cost and territorial control. Real news, and the strongest negative
#: tone in the corpus, but it does not price a barrel: `market_index` was
#: taking "27 killed in Bucha" as a 0.93-confidence bullish reading.
_HUMAN_TOLL = _p(
    r"killed", r"kill", r"dead", r"death", r"deaths", r"toll", r"casualt\w+",
    r"wounded", r"injur\w+", r"died", r"victims?", r"bodies", r"funerals?",
    r"seized", r"captured", r"recaptured", r"advances?", r"retreat\w*",
    r"civilians?", r"warehouses?", r"apartments?", r"residential", r"hospitals?",
)

#: What makes a conflict headline an *oil* headline. A strike on a refinery
#: prices crude; a strike on an apartment block does not.
_ENERGY_NOUN = _p(
    r"oil", r"crude", r"petroleum", r"brent", r"wti", r"refin\w+", r"refiner\w*",
    r"pipelines?", r"tankers?", r"terminals?", r"depots?", r"rigs?", r"wells?",
    r"energy", r"gas", r"lng", r"fuel", r"diesel", r"gasoline", r"petrochem\w*",
    r"exports?", r"barrels?", r"bpd", r"opec\+?", r"strait", r"straits?", r"hormuz",
    r"bab[- ]el[- ]mandeb", r"suez", r"kerch",
)

#: Non-energy business that reaches the filter only because a producer state is
#: named in it -- "AMD, Cisco and Humain expand Saudi Arabia's AI infrastructure".
_OFF_TOPIC = _p(
    r"ai", r"artificial intelligence", r"chatgpt", r"cloud", r"semiconductors?",
    r"chips?", r"datacent\w+", r"data cent\w+", r"advertis\w+", r"ads",
    r"software", r"smartphones?", r"telecoms?", r"e-?commerce", r"streaming",
    r"grain", r"wheat", r"corn", r"soybeans?", r"fertilis\w+", r"fertiliz\w+",
)


#: Pump prices, which are a domestic fiscal decision and not a crude signal.
#: "Iran to increase gasoline prices" is a subsidy cut in Tehran; it scored
#: bullish at 0.81 confidence because the words look exactly like a crude
#: price report to the routing in `events.describes_market_directly`.
_RETAIL_FUEL = re.compile(
    r"(?<!\w)(?:gasoline|petrol|diesel|fuel|pump)\s+prices?(?!\w)", re.I
)


def salience(title: str) -> float:
    """How much this headline can move crude at all, 0..1.

    Separate from direction and from confidence. Confidence asks "how sure are
    we of the call"; salience asks "does the subject matter price a barrel". A
    village changing hands in Donetsk can be reported with total certainty and
    still deserve no weight in an oil index.

    Feeds `HeadlineScore.salience`, which `market_index` already multiplies into
    the weight and which nothing has been populating.
    """
    from . import events  # local import: events imports nothing from here

    text = (title or "").strip()
    if not text:
        return 0.0

    energy = bool(_ENERGY_NOUN.search(text))

    if _RETAIL_FUEL.search(text):
        return 0.2
    if _OFF_TOPIC.search(text) and not energy:
        return 0.1
    # Casualty counts and territorial swaps, with nothing energy-related named.
    if _HUMAN_TOLL.search(text) and not energy:
        return 0.15

    # Otherwise the operator decides: an OPEC decision outweighs a Colombian
    # production print, and `events` already tiers that.
    weight = events._entity_weight(text)
    return weight if energy or weight > 0.4 else 0.4 * weight

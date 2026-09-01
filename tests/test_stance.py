"""Tests for the stance reading, and for the hybrid scorer built on it.

Every case here is a headline the live feed actually produced, with the score
the tone scorer gave it recorded in the comment. They are regression tests in
the strict sense: each one was wrong in production.
"""

import pytest

from cns import events, scoring, stance


class H:
    def __init__(self, title):
        self.title = title


# --- negation -------------------------------------------------------------


@pytest.mark.parametrize("title", [
    # Scored +47.5 bullish. It is CENTCOM saying the disruption did not happen.
    "US CENTCOM: No ships have hit mines in the Strait of Hormuz.",
    # Scored +25.8 bullish. Same shape: a supply loss being denied.
    "Ceo of Iran's National Oil Company: Oil operations have not stopped at Kharg Island - Nour News",
    "Iran denies its forces attacked the tanker",
    # An action prevented lands in the same place as one negated.
    "The US administration warned Israel against carrying out any unilateral strikes against Iran",
])
def test_negated_disruption_detected(title):
    assert stance.negated_disruption(title) is True


@pytest.mark.parametrize("title", [
    # "won't succeed" negates the success, not the sanctions -- and the
    # headline is a demand for *more* of them.
    "Bessent: Iran pressure campaign won't succeed unless Chinese firms face secondary sanctions",
    # "told not to hold funds" is coercion, not a negated disruption.
    "US Treasury Secretary Bessent: banks told not to hold Iranian funds or support Iranian regime",
    # "no target ... beyond reach" is a threat, and `target` is kept out of the
    # disruption vocabulary precisely so this does not invert.
    "Senior Iranian Source: Iran's retaliation against US strikes on Larak showed that no target in the region is beyond Tehran's reach.",
    # "without coordination" is a condition, not a negation.
    "IRGC: Strait of Hormuz is closed to all ships that intend to transit without coordination with Iran.",
    "Iran's President Pezeshkian: war is in no one's interest - Tasnim.",
    # No negation cue at all, and the word `no` must not match inside a name.
    "Russia: seized Novoandriivka in Ukraine's Donetsk region - TASS",
])
def test_negation_not_overreached(title):
    assert stance.negated_disruption(title) is False


# --- stance ---------------------------------------------------------------


@pytest.mark.parametrize("title", [
    # The headline that motivated all of this: FinBERT reads it positive at
    # 0.939 on "thank" and "strong support", and inversion made it -92.2.
    "US Treasury Secretary Bessent: We thank the EU for strong support of actions against Iran.",
    "EU: We will continue to work closely with the united States and other G7 and international partners to maintain pressure on Iran",
    "US Treasury Secretary Bessent: Iran is taking sanctions seriously.",
    "US Treasury Secretary Bessent on Iran: We're going to continue exerting pressure.",
    "IRGC navy: full control over Strait of Hormuz, waterway closed to ships transiting",
    "Iran's Revolutionary Guards: vessels must follow its rules for transit through Strait of Hormuz",
])
def test_coercion_read_through_polite_wording(title):
    assert stance.polarity(title) == stance.COERCION


@pytest.mark.parametrize("title", [
    # `sanctions relief` contains a coercion word and means its opposite, which
    # is why de-escalation is tested first.
    "Iran's President Pezeshkian: Commitments include fuel, petrochem sanctions relief, release of funds, and resumption of investment.",
    "Iran's President Pezeshkian tells India's Prime Minister Tehran still seeks a negotiated solution out of conflict with the US",
    "Senior Iranian Source: Recent hostilities remain a limited and contained confrontation between Iran and Washington.",
    "Trump: United States has just reached an oil pact with Venezuela",
])
def test_deescalation_beats_coercion_vocabulary(title):
    assert stance.polarity(title) == stance.DEESCALATION


def test_blockade_limiting_imports_is_not_deescalation():
    """`limited` is kept out of the de-escalation lexicon on purpose.

    "U.S. blockade ... has limited Chinese imports" is coercion; an earlier
    draft matched bare `limited` for "limited and contained confrontation" and
    flipped this one.
    """
    title = ("US Treasury Secretary Bessent: U.S. blockade of Iran ports "
             "has limited Chinese imports of Iranian oil")
    assert stance.polarity(title) == stance.COERCION


# --- salience -------------------------------------------------------------


@pytest.mark.parametrize("title", [
    # Scored +85.1 bullish at confidence 0.94. Nothing here prices a barrel.
    "Death toll in Russian strike on Kyiv-area warehouse rises to 37: governor",
    "Kyiv region governor: 27 killed in overnight Russian attack on Bucha district",
    "Russia: seized Rubizhne in Ukraine's Donetsk region - TASS",
    # Reaches the filter only because Saudi Arabia is named in it.
    "AMD, Cisco and Humain expand Saudi Arabia's AI infrastructure.",
    "OpenAI: Starting later today, advertisers can purchase ChatGPT ads directly via Ads Manager across India, Europe, the Middle East, and North Africa.",
    # A subsidy decision in Tehran, not a crude signal.
    "Iran's President Pezeshkian: Iran to increase gasoline prices - ISNA",
])
def test_low_salience_subjects(title):
    assert stance.salience(title) < scoring._SALIENCE_FLOOR


@pytest.mark.parametrize("title", [
    "Iran's Revolutionary Guards: Supertanker caught fire, stopped after hitting two naval mines in Strait of Hormuz",
    "Russian defence ministry: Russian forces start preparations for large-scale strikes on Ukraine energy infrastructure",
    "Ukmto: tanker hit by projectile 12 nautical miles north of Oman's Khasab",
])
def test_energy_conflict_keeps_salience(title):
    assert stance.salience(title) >= scoring._SALIENCE_FLOOR


# --- events routing fixes -------------------------------------------------


def test_supply_subject_beats_demand_noun_further_along():
    """`output fell ... of domestic consumption` is a supply loss.

    Taking the nouns in a fixed order read this as falling demand and scored it
    -96.7 bearish at confidence 0.98, when a Russian refining shortfall is
    bullish. The earliest noun is the subject.
    """
    title = ("Russia's Gasoline output fell to about 70% of domestic "
             "consumption at the end of August - Sources")
    assert events.classify(title).kind == events.SUPPLY_DOWN
    assert events.describes_market_directly(title) is False


def test_spr_refill_is_demand_not_a_stock_build():
    """The strategic reserve is supply going out and demand coming in."""
    title = "Trump on Venezuela: plans to replenish strategic national reserves with Venezuelan oil"
    event = events.classify(title)
    assert event.kind == events.DEMAND_UP
    assert event.direction == +1


def test_spr_release_is_still_bearish():
    event = events.classify("US to release 30 million barrels from the strategic petroleum reserve")
    assert event.direction == -1


def test_barrel_as_a_price_unit_is_not_a_supply_event():
    """"below $60 a barrel" is a price, so this must stay a market report."""
    title = "Brent crude tumbles below $60 a barrel"
    assert events.classify(title).kind != events.SUPPLY_DOWN
    assert events.describes_market_directly(title) is True


@pytest.mark.parametrize("title", [
    "NYMEX Diesel September futures settle at $4.3567 a gallon.",
    "Brent crude oil expected to average $85.08 per barrel in 2026 versus $85.22 forecast in July - Poll.",
    "Iraq oil exports in August reached 2.369m bpd - Government Spokesman",
])
def test_level_reports_carry_no_move(title):
    assert events.is_level_report(title) is True


def test_a_stated_move_disqualifies_a_level_report():
    title = "NYMEX WTI crude October futures settle at $83.40 a barrel, down 13 cents, 0.16%."
    assert events.is_level_report(title) is False
    assert events.stated_move_fraction(title) == pytest.approx(0.0016)


# --- the hybrid scorer end to end -----------------------------------------


@pytest.fixture
def hybrid(monkeypatch):
    monkeypatch.setattr(scoring.settings, "scorer_mode", "hybrid")
    return lambda title: scoring.score(H(title))


def test_courtesy_wording_no_longer_flips_the_sign(hybrid):
    """The first headline the user flagged. Was -92.2 bearish."""
    result = hybrid("US Treasury Secretary Bessent: We thank the EU for strong support of actions against Iran.")
    assert result.direction == "bullish"
    assert result.event == "stance:coercion"


def test_negated_disruption_is_bearish(hybrid):
    """The second headline the user flagged. Was +47.5 bullish."""
    result = hybrid("US CENTCOM: No ships have hit mines in the Strait of Hormuz.")
    assert result.direction == "bearish"
    assert result.event == "negated_disruption"


def test_every_headline_gets_a_side(hybrid):
    """The scorer must never abstain -- "no opinion" is not a trading signal.

    Weak headlines are damped, not silenced: the fact that a casualty count
    says little about crude lives in `salience` and `confidence`, where
    `market_index` can act on it, not in a label that erases the call.
    """
    from direction_labels import LABELLED

    for _, title, _ in LABELLED:
        assert hybrid(title).direction in ("bullish", "bearish"), title


def test_casualty_count_is_called_but_barely_weighted(hybrid):
    loud = hybrid("Iran's Revolutionary Guards: Supertanker caught fire, stopped after hitting two naval mines in Strait of Hormuz")
    weak = hybrid("Kyiv region governor: 27 killed in overnight Russian attack on Bucha district")
    assert weak.direction in ("bullish", "bearish")
    assert weak.components["gate"] == "off_topic"
    assert weak.salience < scoring._SALIENCE_FLOOR
    # The whole point: it still gets a call, but a tenth of the weight.
    assert abs(weak.value) * weak.confidence < 0.1 * abs(loud.value) * loud.confidence


def test_settlement_print_is_called_but_barely_weighted(hybrid):
    result = hybrid("NYMEX Diesel September futures settle at $4.3567 a gallon.")
    assert result.direction in ("bullish", "bearish")
    assert result.components["gate"] == "level_report"
    assert abs(result.value) < 20


def test_small_price_move_is_scored_small(hybrid):
    """A 0.16% drift scored -96.3. Magnitude has to come from the number."""
    result = hybrid("NYMEX WTI crude October futures settle at $83.40 a barrel, down 13 cents, 0.16%.")
    assert result.direction == "bearish"
    assert abs(result.value) < 15


def test_salience_is_reported_for_the_index(hybrid):
    """`market_index` multiplies salience in, and nothing was populating it."""
    loud = hybrid("Iran's Revolutionary Guards: Supertanker caught fire, stopped after hitting two naval mines in Strait of Hormuz")
    quiet = hybrid("Russia: seized Rubizhne in Ukraine's Donetsk region - TASS")
    assert loud.salience > quiet.salience
    assert 0.0 <= quiet.salience <= 1.0


def test_hybrid_scores_are_stored_under_their_own_version(monkeypatch):
    """Switching modes must not overwrite the tone scorer's history."""
    monkeypatch.setattr(scoring.settings, "scorer_version", "v1")
    monkeypatch.setattr(scoring.settings, "scorer_mode", "hybrid")
    assert scoring.version() == "v1-hybrid"
    monkeypatch.setattr(scoring.settings, "scorer_mode", "sentiment")
    monkeypatch.setattr(scoring.settings, "scorer_invert", True)
    assert scoring.version() == "v1-sentiment-inv"


def test_obstructed_talks_are_not_deescalation():
    """A named negotiation can be the thing going wrong.

    Found out of sample: this scored bearish at 0.89 confidence because
    `negotiation` matched, when the headline reports the talks being blocked.
    """
    title = ("Iran's revolutionary guards spokesperson: The US is obstructing the "
             "negotiation process between Oman and Iran, which has caused it to be delayed")
    assert stance.polarity(title) != stance.DEESCALATION


@pytest.mark.parametrize("title", [
    "Iran and Oman agree ceasefire terms over the Strait of Hormuz",
    "Iran's President Pezeshkian: Commitments include fuel, petrochem sanctions relief, release of funds, and resumption of investment.",
    "Trump: United States has just reached an oil pact with Venezuela",
])
def test_obstruction_guard_does_not_swallow_real_deescalation(title):
    assert stance.polarity(title) == stance.DEESCALATION


@pytest.mark.parametrize("title,kind", [
    # Scored bullish +84 on "war" and "hit" -- but capacity coming back is
    # supply returning, which is bearish.
    ("Abu Dhabi National Oil Company returns to full capacity after the war hit.", events.SUPPLY_UP),
    ("Libya restores crude exports after pipeline repairs", events.SUPPLY_UP),
    ("Libya declares force majeure on crude exports after pipeline damage", events.SUPPLY_DOWN),
])
def test_capacity_returning_is_supply_up(title, kind):
    assert events.classify(title).kind == kind


def test_returning_needs_a_supply_noun():
    """`returns` is a common word; without a supply noun it must not fire."""
    assert events.classify("Iran returns to negotiations with the US").kind == events.UNKNOWN

"""Supply/demand event classification.

Direction here is about the crude price, not the mood of the sentence. These
tests exist because that distinction is exactly what general sentiment models
get wrong, and what the published CrudeBERT failed to fix.
"""

import pytest

from cns import events


def kind_of(title):
    return events.classify(title).kind


def direction_of(title):
    return events.classify(title).direction


# --- the inversion that motivates the whole module ------------------------


@pytest.mark.parametrize(
    "title",
    [
        "OPEC announces deep cuts to crude production quotas",
        "Saudi Arabia slashes oil output by two million barrels per day",
        "Libya halts crude exports after pipeline damage",
        "Nigeria declares force majeure on crude loadings",
    ],
)
def test_supply_reductions_are_bullish_despite_grim_wording(title):
    """FinBERT rates the first of these negative at 0.85. It is bullish."""
    assert direction_of(title) == +1
    assert kind_of(title) == events.SUPPLY_DOWN


@pytest.mark.parametrize(
    "title",
    [
        "OPEC raises production quotas sharply for next quarter",
        "Saudi Arabia to flood the market with extra crude supply",
        "Russia boosts crude exports to record high",
    ],
)
def test_supply_increases_are_bearish_despite_upbeat_wording(title):
    """FinBERT rates the first of these positive at 0.69. It is bearish."""
    assert direction_of(title) == -1
    assert kind_of(title) == events.SUPPLY_UP


# --- inventories, which read backwards ------------------------------------


def test_inventory_build_is_bearish():
    """A build is oil sitting unused, however positive "rising" sounds."""
    event = events.classify("US crude inventories post a huge unexpected build")
    assert event.kind == events.SUPPLY_UP
    assert event.direction == -1


def test_inventory_draw_is_bullish():
    event = events.classify("US crude stocks post a surprise draw")
    assert event.kind == events.SUPPLY_DOWN
    assert event.direction == +1


def test_inventories_are_not_read_as_plain_supply():
    """Reading a 'stocks rise' headline as supply wording gets it backwards."""
    event = events.classify("Crude stockpiles rise sharply at Cushing")
    assert "inventory_build" in event.matched


def test_inventory_reports_are_never_unattributed():
    """They move the whole market regardless of who is named in the headline."""
    assert events.classify("Crude inventories build sharply").entity_weight >= 0.8


# --- demand ---------------------------------------------------------------


def test_demand_collapse_is_bearish():
    assert kind_of("Global oil demand collapses as recession deepens") == events.DEMAND_DOWN


def test_demand_growth_is_bullish():
    assert kind_of("China crude imports surge to a record") == events.DEMAND_UP


# --- geopolitical risk ----------------------------------------------------


def test_threats_to_supply_are_bullish():
    assert direction_of("Iran threatens to close the Strait of Hormuz") == +1


def test_de_escalation_unwinds_the_premium():
    assert direction_of("Iran and Oman agree ceasefire terms over Hormuz") == -1
    assert direction_of("Iran, Oman agreed on share of Hormuz revenues") == -1


# --- magnitude ------------------------------------------------------------


def test_major_producers_outweigh_minor_ones():
    saudi = events.classify("Saudi Arabia cuts crude output")
    angola = events.classify("Angola cuts crude output")
    assert saudi.entity_weight > angola.entity_weight


def test_intensity_words_raise_magnitude():
    plain = events.classify("OPEC cuts crude output quotas")
    deep = events.classify("OPEC announces deep cuts to crude output quotas")
    assert deep.magnitude > plain.magnitude


def test_stated_volumes_raise_magnitude():
    plain = events.classify("Saudi Arabia cuts crude output")
    sized = events.classify("Saudi Arabia cuts crude output by 5 million bpd")
    assert sized.magnitude > plain.magnitude


def test_spelled_out_numbers_are_read():
    """Wire copy writes "two million barrels" as often as "2 mln"."""
    assert events.classify(
        "Saudi Arabia cuts oil output by two million barrels per day"
    ).magnitude > events.classify("Saudi Arabia cuts oil output").magnitude


def test_proposals_are_discounted_against_decisions():
    decided = events.classify("OPEC cuts output quotas")
    mooted = events.classify("OPEC may consider a proposal to cut output quotas")
    assert mooted.hedged and not decided.hedged
    assert mooted.magnitude < decided.magnitude


# --- abstention -----------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Trump on Iran's Ayatollah: I don't think he's dead.",
        "BOJ governor Ueda will skip this week's Jackson Hole meeting",
        "",
    ],
)
def test_headlines_with_no_event_are_unknown(title):
    """Better to say nothing than to invent a direction."""
    event = events.classify(title)
    assert event.kind == events.UNKNOWN
    assert event.direction == 0

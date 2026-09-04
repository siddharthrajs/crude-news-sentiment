"""Structured prints parsed as data: `cns.templates`, and its place in scoring.

The Hormuz transit series is the reason this layer exists -- see the module
docstring for the three days it got two different answers to the same question.
"""

import pytest

from cns import scoring, templates


class H:
    def __init__(self, title):
        self.title = title


@pytest.fixture
def hybrid(monkeypatch):
    monkeypatch.setattr(scoring.settings, "scorer_mode", "hybrid")
    return lambda title: scoring.score(H(title))


# --- the transit template -------------------------------------------------

#: Every transit headline the live feed has actually produced.
BELOW_AVERAGE = [
    "Five commodity ships pass Strait of Hormuz on Tuesday, well below 10-day average of 15, data shows",
    "Five commodity ships pass Strait of Hormuz on Thursday, versus 10-day average of 15, data shows",
    "Four commodity vessels pass Strait of Hormuz on Tuesday, data shows, compared with 10-day average of around 13",
    "Six commodity ships pass Strait of Hormuz on Wednesday, below 10-day average of about 13, data shows",
    "Four commodity ships pass Strait of Hormuz on Thursday, below 10-day average of about 15, data shows",
]

#: Headlines about Hormuz shipping that are *not* the data print. A template
#: that fires on these is worse than one that never fired at all.
NOT_A_PRINT = [
    "Commodity ships passing through Strait of Hormuz fall to 5 daily over weekend - data",
    "Strait of Hormuz commodity ship passages steady on Monday, stay in single digits - data",
    "Trump suggests 22 ships passed through Hormuz last night",
    "IRGC navy: full control over Strait of Hormuz, waterway closed to ships transiting",
    "US CENTCOM: No ships have hit mines in the Strait of Hormuz.",
]


@pytest.mark.parametrize("title", BELOW_AVERAGE)
def test_traffic_below_average_is_bullish_whatever_the_wording(title):
    """"below", "well below", "versus", "compared with" -- same measurement.

    The wording is exactly what used to decide the sign.
    """
    result = templates.classify(title)
    assert result is not None, title
    assert result.kind == "hormuz_transit"
    assert result.direction == 1


@pytest.mark.parametrize("title", NOT_A_PRINT)
def test_prose_about_hormuz_shipping_is_not_claimed(title):
    assert templates.classify(title) is None


def test_traffic_above_average_is_bearish():
    assert templates.classify(
        "Eighteen commodity ships pass Strait of Hormuz on Friday, above 10-day average of 13, data shows"
    ).direction == -1


def test_magnitude_tracks_the_shortfall():
    """Four of a normal 15 is a worse disruption than six of a normal 13."""
    worse = templates.classify(BELOW_AVERAGE[4])
    milder = templates.classify(BELOW_AVERAGE[3])
    assert worse.magnitude > milder.magnitude
    assert worse.extra == {"count": 4, "average": 15, "shortfall": 0.733}


def test_counts_are_read_as_words_or_digits():
    words = templates.classify(
        "Four commodity ships pass Strait of Hormuz on Monday, below 10-day average of 12, data shows"
    )
    digits = templates.classify(
        "4 commodity ships pass Strait of Hormuz on Monday, below 10-day average of 12, data shows"
    )
    assert words.magnitude == digits.magnitude


def test_a_half_matched_template_declines():
    """No average stated means no reading -- fall through, do not guess one."""
    assert templates.classify("Four commodity ships pass Strait of Hormuz on Tuesday") is None


def test_a_matched_template_never_scores_a_flat_zero():
    """Zero would render as an empty meter, which is what *unscored* looks like."""
    result = templates.classify(
        "Thirteen commodity ships pass Strait of Hormuz on Monday, in line with 10-day average of 13, data shows"
    )
    assert result.magnitude >= templates._MIN_MAGNITUDE
    assert result.direction in (1, -1)


# --- placement in the scorer ----------------------------------------------


def test_template_outranks_the_tone_fallback(hybrid):
    """The regression. This headline scored -86.7 bearish; traffic at under a
    third of normal is one of the most bullish prints the feed can carry."""
    result = hybrid(BELOW_AVERAGE[2])
    assert result.event == "template:hormuz_transit"
    assert result.direction == "bullish"
    assert result.value > 50


def test_the_three_day_series_now_agrees_with_itself(hybrid):
    """09-02 read bearish while 09-03 and 09-04 read bullish, off the same
    measurement. All three are bullish now, by construction rather than luck."""
    series = [hybrid(t) for t in BELOW_AVERAGE[2:5]]
    assert {r.direction for r in series} == {"bullish"}
    assert all(r.confidence > 0.5 for r in series)


# --- level reports --------------------------------------------------------


def test_settlement_levels_barely_register(hybrid):
    """Three of these print every evening and none of them says anything.

    At the old 0.2 damp they were a steady -16 each, a systematic bearish drip
    into the index from headlines with no direction in them.
    """
    for title in (
        "NYMEX Gasoline October futures settle at $3.1351 a gallon.",
        "NYMEX Diesel October futures settle at $4.6773 a gallon.",
        "NYMEX Natural Gas October futures settle at $2.9040/MMBTU.",
    ):
        result = hybrid(title)
        assert result.components["gate"] == "level_report"
        assert abs(result.value) < 5, title


def test_a_settlement_that_states_a_move_is_still_a_real_reading(hybrid):
    """The damp must not swallow the settlements that do carry direction."""
    result = hybrid("NYMEX WTI crude October futures settle at $90.22 a barrel, up $4.46, 5.20%.")
    assert result.components["gate"] is None
    assert result.direction == "bullish"
    assert result.value > 50


@pytest.mark.parametrize("region,basis", [
    ("Asia", "vs Oman/Dubai average"),
    ("NW Europe", "vs ICE Brent settlement"),
    ("US", "vs ASCI"),
])
def test_official_selling_prices_are_levels(hybrid, region, basis):
    """An OSP is where the producer set the price, not a move in it.

    All three used to reach `is_level_report` only by accident, on incidental
    words in the comparison clause -- so the one priced against ASCI, which has
    no such word, was scored as prose at -34.3.
    """
    result = hybrid(
        f"Saudi Arabia sets October Arab Light Crude Oil OSP to {region} at minus $2/bbl {basis} - pricing document."
    )
    assert result.components["gate"] == "level_report"
    assert abs(result.value) < 5

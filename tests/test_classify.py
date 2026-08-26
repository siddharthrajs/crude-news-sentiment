"""Cases are verbatim titles captured from the live feed."""

import pytest

from cns.classify import CALENDAR, NARRATIVE, RESEARCH, WIDGET, classify

CALENDAR_CASES = [
    "US API Crude Oil Stock Change Actual 4.2M (Forecast -, Previous -0.328M)",
    "Swedish PPI YoY Actual 6.4% (Forecast -, Previous 7.4%)",
    "UK CBI Distributive Trades Actual -48 (Forecast -35, Previous -26)",
    "German 15 Yr Bid-to-Cover Actual 2 (Forecast -, Previous 1.8)",
    "Japanese Service PPI Actual 3.6% (Forecast 3.2%, Previous 3.2%)",
]

WIDGET_CASES = [
    "30-Day Correlation Matrix",
    "120-Day Correlation Matrix",
    "FX Implied Volatility",
    "Commodities Implied Volatility",
    "Top S&P 500 Stock Names Implied Volatility",
    "Fed Interest Rate Probabilities",
    "SNB Interest Rate Probabilities",
    "Currency Strength Chart: Strongest: AUD, JPY, USD, EUR, GBP, CAD, CHF, NZD - Weakest",
]

RESEARCH_CASES = [
    "MUFG: The AUD - FJElite",
    "ING: The USD - FJElite",
    "Europe Sentiment: Eyes On NVIDIA - FJElite",
]

NARRATIVE_CASES = [
    "Iran and Oman aim for a permanent Hormuz route in 60 days - Tasnim",
    "Saudi Aramco offers Arab medium, heavy crude oil for September loading to Asian refiners - Sources",
    "Tankers load 4 mln bbls of Saudi Crude in ship-to-ship transfer off Oman, cargoes heading for China - Shipping Data.",
    "Japan plans to diversify oil procurement - Cabinet Office",
    "Five commodity ships pass Strait of Hormuz on Tuesday, well below 10-day average of 15, data shows",
    "ECB's Schnabel: Natural gas situation particularly concerning.",
    # A wire report of the same data as a calendar print, but written as prose.
    "Australia Q2 construction output falls 2.1% q/q, seasonally adjusted (Poll: +0.5%)",
    "Russia downs 426 drones overnight: Russian news agencies cite defence ministry",
]


@pytest.mark.parametrize("title", CALENDAR_CASES)
def test_calendar_prints(title):
    assert classify(title)[0] == CALENDAR


@pytest.mark.parametrize("title", WIDGET_CASES)
def test_auto_posted_widgets(title):
    assert classify(title)[0] == WIDGET


@pytest.mark.parametrize("title", RESEARCH_CASES)
def test_research_teasers(title):
    assert classify(title)[0] == RESEARCH


@pytest.mark.parametrize("title", NARRATIVE_CASES)
def test_real_headlines_survive(title):
    assert classify(title)[0] == NARRATIVE


def test_actual_alone_does_not_trigger_calendar():
    """'Actual' is ordinary English; the '(Forecast' clause is what identifies a print."""
    title = "OPEC says actual production fell short of quota in July"
    assert classify(title)[0] == NARRATIVE


def test_rule_name_is_recorded_for_auditing():
    assert classify("FX Implied Volatility")[1] == "implied_volatility"
    assert classify("Iran seizes tanker near Hormuz")[1] is None


def test_blank_titles_are_safe():
    assert classify("")[0] == NARRATIVE

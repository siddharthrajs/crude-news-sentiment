"""What the poller stores versus what it labels.

The default is to store everything and filter downstream on the labels, because
the feed exposes only a 100-item window: a headline discarded at ingest can
never be fetched again.
"""

import pytest

from cns import poller
from cns.classify import CALENDAR, NARRATIVE, WIDGET
from cns.relevance import GEO_RISK, IRRELEVANT, OIL_DIRECT

OIL = "Saudi Aramco offers Arab medium crude oil for September loading"
GEO = "Iran threatens to close the Strait of Hormuz"
NOISE = "BOJ: governor Ueda will skip this week's Jackson Hole meeting"
PRINT_ = "Swedish PPI YoY Actual 6.4% (Forecast -, Previous 7.4%)"
WIDGET_ = "90-Day Correlation Matrix"


@pytest.fixture
def keep_all(monkeypatch):
    monkeypatch.setattr(poller.settings, "store_irrelevant", True)


@pytest.fixture
def relevant_only(monkeypatch):
    monkeypatch.setattr(poller.settings, "store_irrelevant", False)


def test_relevant_headlines_are_stored_either_way(keep_all):
    assert poller._screen(OIL)[2] == OIL_DIRECT
    assert poller._screen(GEO)[2] == GEO_RISK


def test_nothing_is_discarded_by_default(keep_all):
    """Every headline is kept; the labels carry the filtering decision."""
    for title in (NOISE, PRINT_, WIDGET_):
        assert poller._screen(title) is not None


def test_rejects_are_labelled_not_silently_kept(keep_all):
    kind, _, category, _ = poller._screen(NOISE)
    assert (kind, category) == (NARRATIVE, IRRELEVANT)

    kind, rule, category, _ = poller._screen(PRINT_)
    assert (kind, category) == (CALENDAR, IRRELEVANT)
    assert rule == "actual_forecast_previous"

    assert poller._screen(WIDGET_)[0] == WIDGET


def test_non_narrative_never_gets_a_relevance_category(keep_all):
    """A calendar print mentioning crude is still a print, not an oil headline."""
    kind, _, category, _ = poller._screen(
        "US API Crude Oil Stock Change Actual 4.2M (Forecast -, Previous -0.328M)"
    )
    assert kind == CALENDAR
    assert category == IRRELEVANT


def test_opting_out_discards_everything_irrelevant(relevant_only):
    assert poller._screen(OIL) is not None
    assert poller._screen(GEO) is not None
    for title in (NOISE, PRINT_, WIDGET_):
        assert poller._screen(title) is None


def test_matched_terms_are_persisted_for_relevant_rows(keep_all):
    terms = poller._screen(GEO)[3]
    assert "hormuz" in terms and "iran" in terms
    assert poller._screen(NOISE)[3] is None

"""Scoring in both modes.

sentiment: FinBERT net sentiment of the wording.
event:     supply/demand rules set direction, FinBERT sets intensity only.
"""

import pytest

from cns import scoring


class H:
    def __init__(self, title):
        self.title = title


@pytest.fixture(autouse=True)
def event_mode(monkeypatch):
    """Most tests here cover the event scorer; sentiment has its own block."""
    monkeypatch.setattr(scoring.settings, "scorer_mode", "event")


@pytest.fixture
def no_finbert(monkeypatch):
    """Rules only -- the event scorer must work without torch installed."""
    monkeypatch.setattr(scoring.settings, "finbert_enabled", False)
    monkeypatch.setattr(scoring, "_finbert_probs", lambda title: None)


@pytest.fixture
def loud_finbert(monkeypatch):
    """FinBERT certain the wording is charged, and pointing the wrong way.

    Its direction must be ignored entirely -- these are the real probabilities
    it returns for "OPEC announces deep cuts", which is bullish for crude.
    """
    monkeypatch.setattr(scoring.settings, "finbert_enabled", True)
    monkeypatch.setattr(
        scoring, "_finbert_probs",
        lambda title: {"positive": 0.06, "negative": 0.85, "neutral": 0.09},
    )


def test_supply_cut_scores_bullish_though_finbert_says_negative(loud_finbert):
    """The headline that breaks every general sentiment model."""
    result = scoring.score(H("OPEC announces deep cuts to crude production quotas"))
    assert result.direction == "bullish"
    assert result.value > 0
    assert result.event == "supply_down"
    assert result.components["finbert"]["negative"] == 0.85


def test_supply_increase_scores_bearish(loud_finbert):
    result = scoring.score(H("OPEC raises production quotas sharply for next quarter"))
    assert result.direction == "bearish"
    assert result.value < 0


def test_finbert_changes_size_not_sign(monkeypatch, no_finbert):
    quiet = scoring.score(H("OPEC cuts crude output quotas"))
    monkeypatch.setattr(scoring.settings, "finbert_enabled", True)
    monkeypatch.setattr(
        scoring, "_finbert_probs",
        lambda t: {"positive": 0.5, "negative": 0.5, "neutral": 0.0},
    )
    loud = scoring.score(H("OPEC cuts crude output quotas"))
    assert quiet.direction == loud.direction == "bullish"
    assert loud.value > quiet.value


def test_unreadable_headline_scores_none_not_zero(no_finbert):
    """None means "no event found"; 0.0 would mean "genuinely balanced".

    Collapsing them would let unscored headlines drag the index toward neutral.
    """
    assert scoring.score(H("Trump on Iran's Ayatollah: I don't think he's dead.")) is None


def test_scores_stay_in_range(no_finbert):
    for title in [
        "OPEC announces deep record cuts of 10 million bpd to crude output",
        "Saudi Arabia floods the market with a massive 10 million bpd supply glut",
    ]:
        result = scoring.score(H(title))
        assert -100.0 <= result.value <= 100.0


def test_hedged_headlines_score_lower_and_less_confidently(no_finbert):
    decided = scoring.score(H("OPEC cuts crude output quotas"))
    mooted = scoring.score(H("OPEC may consider a proposal to cut crude output quotas"))
    assert abs(mooted.value) < abs(decided.value)
    assert mooted.confidence < decided.confidence


def test_major_producer_scores_higher_than_minor(no_finbert):
    saudi = scoring.score(H("Saudi Arabia cuts crude output"))
    angola = scoring.score(H("Angola cuts crude output"))
    assert abs(saudi.value) > abs(angola.value)


def test_components_are_recorded_for_auditing(no_finbert):
    result = scoring.score(H("Saudi Arabia slashes crude output"))
    assert result.components["event"] == "supply_down"
    assert result.components["entity_weight"] == 1.0
    assert "matched" in result.components


def test_rules_only_still_produces_a_direction(no_finbert):
    """Without torch the scorer degrades in strength, not in correctness."""
    result = scoring.score(H("Libya halts crude exports after pipeline damage"))
    assert result.direction == "bullish"
    assert result.components["finbert"] is None


def test_finbert_failure_falls_back_to_rules(monkeypatch):
    """A model fault must not take the event pipeline down."""
    monkeypatch.setattr(scoring.settings, "finbert_enabled", True)
    monkeypatch.setattr(scoring, "_finbert_probs", lambda title: None)
    result = scoring.score(H("OPEC cuts crude output quotas"))
    assert result is not None and result.direction == "bullish"


# --- sentiment mode -------------------------------------------------------


@pytest.fixture
def sentiment_mode(monkeypatch):
    monkeypatch.setattr(scoring.settings, "scorer_mode", "sentiment")
    monkeypatch.setattr(scoring.settings, "scorer_invert", False)


@pytest.fixture
def inverted_mode(monkeypatch):
    monkeypatch.setattr(scoring.settings, "scorer_mode", "sentiment")
    monkeypatch.setattr(scoring.settings, "scorer_invert", True)


def finbert(monkeypatch, positive, negative, neutral):
    monkeypatch.setattr(
        scoring, "_finbert_probs",
        lambda title: {"positive": positive, "negative": negative, "neutral": neutral},
    )


def test_sentiment_is_net_positive_minus_negative(sentiment_mode, monkeypatch):
    finbert(monkeypatch, 0.80, 0.10, 0.10)
    result = scoring.score(H("Oil prices rally on strong demand"))
    assert result.direction == "bullish"
    assert result.value == pytest.approx(70.0)


def test_sentiment_reads_negative_wording_as_bearish(sentiment_mode, monkeypatch):
    finbert(monkeypatch, 0.06, 0.85, 0.09)
    result = scoring.score(H("Oil slides as demand falters"))
    assert result.direction == "bearish"
    assert result.value == pytest.approx(-79.0)


def test_uninverted_sentiment_gets_supply_cuts_backwards(sentiment_mode, monkeypatch):
    """Why SCORER_INVERT exists: a supply cut sounds grim and lifts the price."""
    finbert(monkeypatch, 0.06, 0.85, 0.09)
    assert scoring.score(H("OPEC announces deep cuts to crude production")).direction == "bearish"


def test_inversion_fixes_supply_and_risk_headlines(inverted_mode, monkeypatch):
    """Measured 11/12 on supply and risk headlines, against 0/12 uninverted."""
    finbert(monkeypatch, 0.06, 0.85, 0.09)
    result = scoring.score(H("OPEC announces deep cuts to crude production"))
    assert result.direction == "bullish"
    assert result.value > 0
    assert result.components["inverted"] is True


def test_market_reports_are_routed_away_from_the_flip(inverted_mode, monkeypatch):
    """Inverting these was the flip's whole cost -- measured 0/6 before routing.

    "Global oil demand collapses" is negative *and* bearish, and "Brent tumbles
    below $60" reports a fall. Routing on cns.events.describes_market_directly
    keeps their sign, taking the pair from 0/6 to 6/6 and the overall eval from
    11/18 to 17/18.
    """
    finbert(monkeypatch, 0.03, 0.94, 0.03)
    for title in (
        "Global oil demand collapses as recession deepens",
        "Brent crude tumbles below $60 a barrel",
        "US crude inventories post a huge unexpected build",
    ):
        result = scoring.score(H(title))
        assert result.direction == "bearish", title
        assert result.components["inverted"] is False
        assert result.components["describes_market_directly"] is True


def test_supply_events_still_get_flipped(inverted_mode, monkeypatch):
    """Routing must not disarm the flip where it was doing the work."""
    finbert(monkeypatch, 0.06, 0.85, 0.09)
    result = scoring.score(H("Libya halts crude exports after pipeline damage"))
    assert result.direction == "bullish"
    assert result.components["inverted"] is True


def test_inversion_is_recorded_in_the_stored_version(monkeypatch):
    """Inverted and plain scores must never share a version label."""
    monkeypatch.setattr(scoring.settings, "scorer_version", "v0")
    monkeypatch.setattr(scoring.settings, "scorer_mode", "sentiment")
    monkeypatch.setattr(scoring.settings, "scorer_invert", True)
    assert scoring.version() == "v0-sentiment-inv"
    monkeypatch.setattr(scoring.settings, "scorer_invert", False)
    assert scoring.version() == "v0-sentiment"


def test_inversion_does_not_touch_event_mode(monkeypatch):
    """Event mode takes direction from rules, so there is nothing to flip."""
    monkeypatch.setattr(scoring.settings, "scorer_mode", "event")
    monkeypatch.setattr(scoring.settings, "scorer_invert", True)
    monkeypatch.setattr(scoring, "_finbert_probs", lambda t: None)
    assert scoring.score(H("OPEC cuts crude output quotas")).direction == "bullish"
    # Pinned, because `scorer_version` is read from the environment and a
    # deployed .env setting it to anything but the default failed this.
    monkeypatch.setattr(scoring.settings, "scorer_version", "v0")
    assert scoring.version() == "v0-event"


def test_balanced_wording_is_called_neutral(sentiment_mode, monkeypatch):
    finbert(monkeypatch, 0.45, 0.42, 0.13)
    assert scoring.score(H("OPEC meets on Tuesday")).direction == "neutral"


def test_confidence_is_the_non_neutral_mass(sentiment_mode, monkeypatch):
    finbert(monkeypatch, 0.10, 0.10, 0.80)
    assert scoring.score(H("Routine update")).confidence == pytest.approx(0.20)


def test_sentiment_needs_finbert(sentiment_mode, monkeypatch):
    """Unlike event mode, there is no rule fallback -- abstain instead."""
    monkeypatch.setattr(scoring, "_finbert_probs", lambda title: None)
    assert scoring.score(H("Oil prices rally")) is None


def test_mode_is_part_of_the_stored_version(monkeypatch):
    """Switching modes must not silently mix two scorers under one label."""
    monkeypatch.setattr(scoring.settings, "scorer_version", "v0")
    monkeypatch.setattr(scoring.settings, "scorer_invert", False)
    monkeypatch.setattr(scoring.settings, "scorer_mode", "sentiment")
    assert scoring.version() == "v0-sentiment"
    monkeypatch.setattr(scoring.settings, "scorer_mode", "event")
    assert scoring.version() == "v0-event"


def test_blank_titles_score_nothing(sentiment_mode):
    assert scoring.score(H("   ")) is None

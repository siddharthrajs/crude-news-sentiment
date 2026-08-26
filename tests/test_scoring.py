"""Scoring: event sets direction, FinBERT only supplies intensity."""

import pytest

from cns import scoring


class H:
    def __init__(self, title):
        self.title = title


@pytest.fixture
def no_finbert(monkeypatch):
    """Rules only -- the scorer must work without torch installed."""
    monkeypatch.setattr(scoring.settings, "finbert_enabled", False)


@pytest.fixture
def loud_finbert(monkeypatch):
    """FinBERT certain the wording is charged, and pointing the wrong way.

    Its direction must be ignored entirely -- these are the real probabilities
    it returns for "OPEC announces deep cuts", which is bullish for crude.
    """
    monkeypatch.setattr(scoring.settings, "finbert_enabled", True)
    monkeypatch.setattr(
        scoring, "_finbert_intensity",
        lambda title: (0.95, {"positive": 0.06, "negative": 0.85, "neutral": 0.09}),
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
    monkeypatch.setattr(scoring, "_finbert_intensity", lambda t: (1.0, {}))
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
    """A model fault must not take the pipeline down."""
    monkeypatch.setattr(scoring.settings, "finbert_enabled", True)

    def boom(title):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(scoring, "_load", boom)
    result = scoring.score(H("OPEC cuts crude output quotas"))
    assert result is not None and result.direction == "bullish"

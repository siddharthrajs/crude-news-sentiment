"""Zero-shot decision logic, with the model stubbed out.

The transformer itself is not exercised here -- torch is an optional extra and
the base test suite must run without it. What is pinned is the decision rule,
which is where the real mistakes were made: the first two versions of it were
both wrong in ways the model was not responsible for.
"""

import pytest

from cns import zeroshot
from cns.relevance import GEO_RISK, IRRELEVANT, OIL_DIRECT

OIL_H = zeroshot.HYPOTHESES[OIL_DIRECT]
GEO_H = zeroshot.HYPOTHESES[GEO_RISK]
NOISE_H = zeroshot.HYPOTHESES[IRRELEVANT]


@pytest.fixture
def fake_model(monkeypatch):
    """Replace the pipeline with one returning scores we choose."""

    def install(*score_sets):
        def fake_pipeline(titles, **kwargs):
            out = []
            for scores in score_sets:
                pairs = sorted(scores.items(), key=lambda kv: -kv[1])
                out.append({"labels": [k for k, _ in pairs], "scores": [v for _, v in pairs]})
            return out if len(out) > 1 else out[0]

        monkeypatch.setattr(zeroshot, "_load", lambda: fake_pipeline)

    return install


def test_confident_oil_headline(fake_model):
    fake_model({OIL_H: 0.93, GEO_H: 0.04, NOISE_H: 0.03})
    verdict = zeroshot.classify("Saudi Aramco offers crude for September loading")
    assert verdict.category == OIL_DIRECT


def test_confident_noise(fake_model):
    fake_model({NOISE_H: 0.68, GEO_H: 0.20, OIL_H: 0.12})
    assert zeroshot.classify("Microsoft 365 maintenance complete").category == IRRELEVANT


def test_split_across_relevant_labels_still_counts_as_relevant(fake_model):
    """The bug that made the first decision rule wrong.

    Real case: "Trump says nuclear deal with Saudi Arabia..." scored geo 0.48 /
    oil 0.44. Neither wins outright, but 0.92 of the mass says it is not noise.
    A top-1 rule called that irrelevant.
    """
    fake_model({GEO_H: 0.48, OIL_H: 0.44, NOISE_H: 0.08})
    verdict = zeroshot.classify("Trump says nuclear deal with Saudi Arabia will advance")
    assert verdict.category == GEO_RISK
    assert verdict.score == pytest.approx(0.92)


def test_larger_relevant_side_picks_the_category(fake_model):
    fake_model({OIL_H: 0.50, GEO_H: 0.42, NOISE_H: 0.08})
    assert zeroshot.classify("x").category == OIL_DIRECT


def test_threshold_is_above_chance(monkeypatch):
    """Two relevant labels against one puts combined mass near 0.67 at chance.

    A threshold below that keeps essentially everything -- measured at 0.50 the
    filter kept 57 of 79 headlines at precision 0.49.
    """
    from cns.config import settings

    assert settings.zeroshot_threshold > 0.67


def test_borderline_mass_is_rejected(fake_model):
    fake_model({OIL_H: 0.40, GEO_H: 0.35, NOISE_H: 0.25})  # 0.75 combined
    assert zeroshot.classify("x").category == IRRELEVANT


def test_all_scores_are_kept_for_auditing(fake_model):
    fake_model({OIL_H: 0.93, GEO_H: 0.04, NOISE_H: 0.03})
    scores = zeroshot.classify("x").scores
    assert set(scores) == {OIL_DIRECT, GEO_RISK, IRRELEVANT}
    assert scores[OIL_DIRECT] == pytest.approx(0.93)


def test_empty_batch_needs_no_model():
    assert zeroshot.classify_batch([]) == []


def test_missing_dependencies_are_reported_not_crashed():
    assert isinstance(zeroshot.is_available(), bool)

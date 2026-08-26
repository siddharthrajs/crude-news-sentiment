"""Relevance filter accuracy, measured against the checked-in eval set.

The thresholds below are a ratchet: they encode where the filter is today so a
lexicon change cannot silently make it worse. Raise them when it improves.
"""

import pytest

from cns.relevance import GEO_RISK, IRRELEVANT, OIL_DIRECT, classify
from tests.labels import LABELLED


def _confusion():
    fp = fn = swap = 0
    for title, want in LABELLED:
        got, _ = classify(title)
        if got == want:
            continue
        if want == IRRELEVANT:
            fp += 1
        elif got == IRRELEVANT:
            fn += 1
        else:
            swap += 1
    return fp, fn, swap


def test_no_relevant_headline_is_dropped():
    """False negatives are unrecoverable -- the row is never stored.

    This is the assertion that matters most: everything else is tunable later,
    but a headline dropped at ingest cannot be recovered, because the feed only
    exposes a 100-item window.
    """
    _, fn, _ = _confusion()
    assert fn == 0


def test_noise_is_kept_out():
    fp, _, _ = _confusion()
    assert fp <= 2


def test_overall_accuracy_does_not_regress():
    fp, fn, swap = _confusion()
    accuracy = (len(LABELLED) - fp - fn - swap) / len(LABELLED)
    assert accuracy >= 0.95


@pytest.mark.parametrize(
    "title",
    [
        "Saudi Aramco offers Arab medium, heavy crude oil for September loading",
        "Tankers load 4 mln bbls of Saudi Crude in ship-to-ship transfer off Oman",
        "US crude inventories build as refinery runs slow",
    ],
)
def test_oil_wins_when_both_match(title):
    """Oil is the more direct signal, so it takes precedence over geo."""
    assert classify(title)[0] == OIL_DIRECT


def test_chokepoints_pass_without_a_risk_word():
    assert classify("Five commodity ships pass Strait of Hormuz on Tuesday")[0] == GEO_RISK


def test_tier2_actor_needs_a_risk_term():
    """Naming a producer is not enough on its own."""
    assert classify("Qatar's emir opens a new airport terminal")[0] == IRRELEVANT
    assert classify("Qatar halts LNG shipments after drone attack")[0] == OIL_DIRECT
    assert classify("Israel and Lebanon agree ceasefire terms")[0] == GEO_RISK


def test_producer_nation_alone_is_not_a_signal():
    """The case the tiering exists for: a top-five producer, zero oil content."""
    title = "Canada's PM Carney set to address the EU Parliament in mid-September"
    assert classify(title)[0] == IRRELEVANT


def test_matched_terms_are_recorded_for_auditing():
    category, terms = classify("Iran threatens to close the Strait of Hormuz")
    assert category == GEO_RISK
    assert "hormuz" in terms and "iran" in terms
    assert classify("Fed holds rates steady")[1] == []


def test_blank_title_is_irrelevant():
    assert classify("")[0] == IRRELEVANT

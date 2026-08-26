from cns import market_index
from tests.conftest import NOW, VERSION


def idx(session, **kw):
    return market_index.compute(session, scorer_version=VERSION, now=NOW, **kw)


def test_no_data_is_reported_not_faked_as_neutral(session):
    result = idx(session)
    assert result.label == "no data"
    assert result.volume == 0


def test_index_is_volume_independent(session, add_scored):
    """Ten copies of the same sentiment must not read as more bullish than two."""
    for _ in range(2):
        add_scored(50.0)
    two = idx(session).index_value

    for _ in range(8):
        add_scored(50.0)
    ten = idx(session).index_value

    assert two == ten == 50.0


def test_recent_headlines_outweigh_old_ones(session, add_scored):
    add_scored(100.0, age_hours=1)     # ~full weight
    add_scored(-100.0, age_hours=144)  # 6 days -> 0.5**6 weight
    result = idx(session, half_life_hours=24.0)
    assert result.index_value > 80


def test_split_market_is_distinguishable_from_quiet_market(session, add_scored):
    add_scored(90.0)
    add_scored(-90.0)
    split = idx(session)

    assert abs(split.index_value) < 1        # cancels to ~neutral
    assert split.dispersion == 90.0          # but disagreement is loud
    assert split.volume == 2


def test_effective_n_collapses_when_one_headline_dominates(session, add_scored):
    add_scored(80.0, age_hours=0)            # full weight
    for _ in range(9):
        add_scored(80.0, age_hours=240)      # 10 days stale, negligible weight
    result = idx(session, window_days=30, half_life_hours=24.0)
    assert result.volume == 10
    assert result.effective_n < 1.5          # really driven by one headline


def test_weights_scale_influence(session, add_scored):
    add_scored(100.0, confidence=1.0, salience=1.0)
    add_scored(-100.0, confidence=0.1, salience=0.1)
    assert idx(session).index_value > 90


def test_zero_weight_rows_do_not_break_aggregation(session, add_scored):
    add_scored(100.0)
    add_scored(-100.0, confidence=0.0)
    assert idx(session).index_value == 100.0


def test_index_stays_within_score_bounds(session, add_scored):
    for _ in range(50):
        add_scored(100.0, age_hours=1)
    assert idx(session).index_value <= 100.0


def test_category_and_window_are_respected(session, add_scored):
    add_scored(100.0, category="oil_direct")
    add_scored(-100.0, category="geo_risk")
    add_scored(-100.0, category="oil_direct", age_hours=24 * 30)  # outside 7d
    assert idx(session).index_value == 100.0
    assert idx(session, category="geo_risk").index_value == -100.0


def test_zscore_is_none_until_baseline_exists(session, add_scored):
    add_scored(50.0)
    assert idx(session).zscore is None

from datetime import datetime

from cns.sources.financial_juice import parse

SAMPLE = """<rss version="2.0"><channel><title>FinancialJuice</title>
<item>
  <title>FinancialJuice: Iran says Strait of Hormuz traffic is normal</title>
  <link>https://www.financialjuice.com/News/1/x.aspx?xy=rss</link>
  <pubDate>Wed, 26 Aug 2026 10:24:14 GMT</pubDate>
  <guid isPermaLink="false">9736857</guid>
</item>
<item>
  <title>No prefix headline</title>
  <link>https://www.financialjuice.com/News/2/y.aspx?xy=rss</link>
  <pubDate>Wed, 26 Aug 2026 10:00:00 GMT</pubDate>
  <guid isPermaLink="false">9736829</guid>
</item>
</channel></rss>"""


def test_parses_items_and_strips_source_prefix():
    items = parse(SAMPLE)
    assert len(items) == 2
    assert items[0].external_id == "9736857"
    assert items[0].title == "Iran says Strait of Hormuz traffic is normal"
    assert items[0].raw_title.startswith("FinancialJuice:")
    assert items[0].published_at == datetime(2026, 8, 26, 10, 24, 14)


def test_leaves_unprefixed_titles_alone():
    assert parse(SAMPLE)[1].title == "No prefix headline"


def test_ignores_entries_without_id_or_title():
    broken = '<rss version="2.0"><channel><item><link>http://x</link></item></channel></rss>'
    assert parse(broken) == []


def test_retry_is_skipped_when_it_would_outlast_the_next_poll(monkeypatch):
    """At a short interval, sleeping through a 429 costs more than it saves.

    A 60s retry at a 61s cadence outlives the next tick, which the scheduler
    then skips, turning 61s into ~122s.
    """
    from cns.config import settings
    from cns.sources import financial_juice

    monkeypatch.setattr(settings, "poll_interval_seconds", 61)
    assert not financial_juice._should_retry(60)
    assert not financial_juice._should_retry(41)
    assert financial_juice._should_retry(5)


def test_retry_still_happens_at_a_long_interval(monkeypatch):
    from cns.config import settings
    from cns.sources import financial_juice

    monkeypatch.setattr(settings, "poll_interval_seconds", 300)
    assert financial_juice._should_retry(60)

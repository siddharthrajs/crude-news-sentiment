"""Split narrative headlines from machine-generated feed noise.

Roughly 38% of the FinancialJuice feed is not a headline at all. Three classes,
all derived from inspecting real captured items rather than guessed at:

* ``calendar``  -- economic data prints, ``... Actual X (Forecast Y, Previous Z)``
* ``widget``    -- recurring auto-posted chart dumps with no prose content,
                   e.g. ``90-Day Correlation Matrix``, ``FX Implied Volatility``
* ``research``  -- FJElite teaser links to paywalled research notes

Only ``narrative`` items continue down the pipeline.

Classification is deterministic and derived purely from the title, so unlike
scores it is safe to store as a column and recompute in place: rerunning the
rules on the whole corpus is idempotent and loses nothing.
"""

from __future__ import annotations

import re

NARRATIVE = "narrative"
CALENDAR = "calendar"
WIDGET = "widget"
RESEARCH = "research"

#: (kind, rule name, pattern). Evaluated in order; first match wins.
_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    # "US API Crude Oil Stock Change Actual 4.2M (Forecast -, Previous -0.328M)"
    # The "(Forecast" clause is what makes this reliable -- "actual" alone is a
    # common English word and matches plenty of real headlines.
    (CALENDAR, "actual_forecast_previous", re.compile(r"\bActual\b.*\(\s*Forecast\b", re.I)),
    # "30-Day Correlation Matrix"
    (WIDGET, "correlation_matrix", re.compile(r"^\d+-Day Correlation Matrix\s*$", re.I)),
    # "FX Implied Volatility", "Top S&P 500 Stock Names Implied Volatility"
    (WIDGET, "implied_volatility", re.compile(r"^.{0,60}\bImplied Volatility\s*$", re.I)),
    # "BoJ Interest Rate Probabilities"
    (WIDGET, "rate_probabilities", re.compile(r"^.{0,40}\bInterest Rate Probabilities\s*$", re.I)),
    # "Currency Strength Chart: Strongest: AUD, JPY ... - Weakest"
    (WIDGET, "currency_strength", re.compile(r"^Currency Strength Chart\b", re.I)),
    # "MUFG: The AUD - FJElite", "Europe Sentiment: Eyes On NVIDIA - FJElite"
    (RESEARCH, "fjelite", re.compile(r"-\s*FJ\s?Elite\s*$", re.I)),
)


def classify(title: str) -> tuple[str, str | None]:
    """Return ``(kind, rule_name)``. ``rule_name`` is None for narrative items."""
    text = (title or "").strip()
    for kind, rule, pattern in _RULES:
        if pattern.search(text):
            return kind, rule
    return NARRATIVE, None


def is_narrative(title: str) -> bool:
    return classify(title)[0] == NARRATIVE

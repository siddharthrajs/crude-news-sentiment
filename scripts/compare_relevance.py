"""Compare the lexicon and the zero-shot model against the hand-labelled set.

Run with the ML venv, which has torch/transformers installed.
"""

import io
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from cns import relevance, zeroshot  # noqa: E402
from tests.labels import LABELLED  # noqa: E402


def score(name, predictions):
    correct = sum(1 for (_, want), got in zip(LABELLED, predictions) if want == got)
    missed = [
        (t, want, got)
        for (t, want), got in zip(LABELLED, predictions)
        if want != relevance.IRRELEVANT and got == relevance.IRRELEVANT
    ]
    noise = [
        (t, want, got)
        for (t, want), got in zip(LABELLED, predictions)
        if want == relevance.IRRELEVANT and got != relevance.IRRELEVANT
    ]
    print(
        "%-12s accuracy %5.1f%%   missed(dropped) %2d   noise(kept) %2d"
        % (name, correct / len(LABELLED) * 100, len(missed), len(noise))
    )
    return missed, noise


titles = [t for t, _ in LABELLED]

lex = [relevance.classify(t)[0] for t in titles]

t0 = time.time()
verdicts = zeroshot.classify_batch(titles)
zs = [v.category for v in verdicts]
elapsed = time.time() - t0

print("zero-shot: %d headlines in %.0fs (incl. model load)\n" % (len(titles), elapsed))

lex_missed, lex_noise = score("lexicon", lex)
zs_missed, zs_noise = score("zero-shot", zs)

both = [
    a if a != relevance.IRRELEVANT else b for a, b in zip(lex, zs)
]
score("either", both)

print("\n--- zero-shot drops these (lexicon and I both say relevant) ---")
for t, want, _ in zs_missed:
    v = verdicts[titles.index(t)]
    print("  want=%-10s %s" % (want, t[:72]))
    print("            %s" % {k: round(x, 2) for k, x in v.scores.items()})

print("\n--- zero-shot keeps these (I say noise) ---")
for t, _, got in zs_noise[:10]:
    v = verdicts[titles.index(t)]
    print("  got=%-10s %.2f | %s" % (got, v.score, t[:70]))

agree = sum(1 for a, b in zip(lex, zs) if a == b)
print("\nlexicon vs zero-shot agree on %d/%d (%.0f%%)" % (agree, len(titles), agree / len(titles) * 100))

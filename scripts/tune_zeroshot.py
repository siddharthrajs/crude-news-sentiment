"""Sweep zero-shot framings and thresholds against the hand-labelled set.

Answers two questions: does scoring each label independently (multi_label) beat
a softmax over all labels, and where should the threshold sit.
"""

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from transformers import pipeline  # noqa: E402

from cns.relevance import IRRELEVANT  # noqa: E402
from tests.labels import LABELLED  # noqa: E402

TITLES = [t for t, _ in LABELLED]
TRUTH = [w for _, w in LABELLED]
RELEVANT = [w != IRRELEVANT for w in TRUTH]

OIL = "crude oil supply, demand, prices, refining or shipping"
GEO = "war, military conflict, sanctions or political crisis involving an oil-producing country"
NOISE = "routine business, economic data, central banks, technology or domestic politics"
TEMPLATE = "This news headline is about {}."

clf = pipeline("zero-shot-classification", model="facebook/bart-large-mnli", device=-1)


def run(labels, multi_label):
    out = clf(TITLES, candidate_labels=labels, hypothesis_template=TEMPLATE,
              multi_label=multi_label)
    if isinstance(out, dict):
        out = [out]
    return [dict(zip(o["labels"], o["scores"])) for o in out]


def evaluate(name, scored, key):
    print("\n%s" % name)
    print("  thresh   recall   precision   accuracy   kept")
    best = None
    for thresh in [round(0.05 * i, 2) for i in range(2, 20)]:
        pred = [key(s) >= thresh for s in scored]
        tp = sum(1 for p, r in zip(pred, RELEVANT) if p and r)
        fp = sum(1 for p, r in zip(pred, RELEVANT) if p and not r)
        fn = sum(1 for p, r in zip(pred, RELEVANT) if not p and r)
        recall = tp / (tp + fn) if tp + fn else 0
        precision = tp / (tp + fp) if tp + fp else 0
        acc = sum(1 for p, r in zip(pred, RELEVANT) if p == r) / len(pred)
        f1 = 2 * recall * precision / (recall + precision) if recall + precision else 0
        flag = ""
        if best is None or f1 > best[0]:
            best, flag = (f1, thresh), "  <-- best F1"
        print("   %.2f     %.2f      %.2f        %.2f      %2d%s"
              % (thresh, recall, precision, acc, sum(pred), flag))
    return best


softmax3 = run([OIL, GEO, NOISE], multi_label=False)
evaluate("softmax over 3 labels, relevant = oil + geo mass",
         softmax3, lambda s: s[OIL] + s[GEO])

multi3 = run([OIL, GEO, NOISE], multi_label=True)
evaluate("independent scores, 3 labels, relevant = max(oil, geo)",
         multi3, lambda s: max(s[OIL], s[GEO]))

multi2 = run([OIL, GEO], multi_label=True)
evaluate("independent scores, 2 labels (no noise label), relevant = max(oil, geo)",
         multi2, lambda s: max(s[OIL], s[GEO]))

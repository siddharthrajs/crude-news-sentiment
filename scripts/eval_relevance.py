"""Report relevance-filter accuracy against the checked-in eval set."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from cns.relevance import classify, IRRELEVANT
from tests.labels import LABELLED

errs = []
fp = fn = swap = 0
for title, want in LABELLED:
    got, terms = classify(title)
    if got != want:
        errs.append((want, got, terms, title))
        if want == IRRELEVANT: fp += 1
        elif got == IRRELEVANT: fn += 1
        else: swap += 1

n = len(LABELLED)
rel = sum(1 for _, w in LABELLED if w != IRRELEVANT)
print("labelled: %d   correct: %d   accuracy: %.1f%%" % (n, n - len(errs), (n - len(errs)) / n * 100))
print("relevant in truth: %d" % rel)
print("false positives (noise kept):        %d" % fp)
print("false negatives (DROPPED FOREVER):   %d" % fn)
print("category swaps (oil <-> geo):        %d" % swap)
if errs:
    print("\nmisses:")
    for want, got, terms, t in errs:
        print("  want=%-10s got=%-10s %-24s | %s" % (want, got, ",".join(terms)[:22], t[:80]))

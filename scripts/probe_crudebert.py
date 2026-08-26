"""Determine CrudeBERT's true output-index -> label mapping empirically.

The shipped config contradicts itself:
    id2label  {-1: negative, 0: neutral, 1: positive}   <- -1 is not an output index
    label2id  {negative: 1, neutral: 2, positive: 0}

Only one can be right, and transformers' pipeline() trusts id2label, so getting
this wrong silently inverts every score. This runs headlines whose direction is
not in doubt and reports which index actually fires.
"""

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import torch  # noqa: E402
from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: E402

MODEL = "Captain-1337/CrudeBERT"

# Direction here is about the crude price, not the mood of the sentence.
# Supply down or demand up is bullish; supply up or demand down is bearish.
CASES = [
    ("OPEC announces deep cuts to crude production quotas", "bullish"),
    ("Saudi Arabia slashes oil output by two million barrels per day", "bullish"),
    ("Attack halts all oil exports from Libya's largest terminal", "bullish"),
    ("US sanctions block Iranian crude exports entirely", "bullish"),
    ("OPEC raises production quotas sharply for next quarter", "bearish"),
    ("Saudi Arabia to flood the market with extra crude supply", "bearish"),
    ("Global oil demand collapses as recession deepens", "bearish"),
    ("US crude inventories post a huge unexpected build", "bearish"),
]

tokenizer = AutoTokenizer.from_pretrained(MODEL)
model = AutoModelForSequenceClassification.from_pretrained(MODEL)
model.eval()

print("logit dimension:", model.config.num_labels)
print()

tally = {}
for text, expected in CASES:
    with torch.no_grad():
        logits = model(**tokenizer(text, return_tensors="pt", truncation=True)).logits
    probs = torch.softmax(logits, dim=-1)[0].tolist()
    top = int(max(range(len(probs)), key=lambda i: probs[i]))
    tally.setdefault(expected, []).append(top)
    print("%-8s idx=%d  probs=[%s]  %s" % (
        expected, top, ", ".join("%.3f" % p for p in probs), text[:58]
    ))

print()
for expected, indices in tally.items():
    counts = {i: indices.count(i) for i in set(indices)}
    print("%s headlines -> index counts %s" % (expected, counts))

print()
print("If bullish and bearish map to different indices, that is the real mapping.")
print("The remaining index is neutral.")

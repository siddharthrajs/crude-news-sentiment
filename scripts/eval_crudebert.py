"""Does CrudeBERT actually discriminate on our headlines?

Runs the stored crude-relevant corpus plus a set of unambiguous control
headlines, and reports how much the output varies. A model that returns nearly
the same distribution regardless of input cannot drive a score, whatever its
label mapping turns out to be.
"""

import io
import statistics
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import torch  # noqa: E402
from sqlalchemy import select  # noqa: E402
from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: E402

from cns.db import SessionLocal  # noqa: E402
from cns.models import Headline  # noqa: E402

MODELS = ["Captain-1337/CrudeBERT", "ProsusAI/finbert"]

CONTROLS = [
    ("OPEC announces deep cuts to crude production quotas", "bullish"),
    ("Saudi Arabia slashes oil output by two million barrels per day", "bullish"),
    ("War shuts down all oil exports from the Persian Gulf", "bullish"),
    ("OPEC raises production quotas sharply for next quarter", "bearish"),
    ("Global oil demand collapses as recession deepens", "bearish"),
    ("US crude inventories post a huge unexpected build", "bearish"),
]


def probs_for(model, tokenizer, texts):
    out = []
    for text in texts:
        with torch.no_grad():
            logits = model(**tokenizer(text, return_tensors="pt", truncation=True)).logits
        out.append(torch.softmax(logits, dim=-1)[0].tolist())
    return out


with SessionLocal() as session:
    corpus = [
        h.title
        for h in session.scalars(
            select(Headline)
            .where(Headline.kind == "narrative", Headline.category != "irrelevant")
            .order_by(Headline.published_at.desc())
            .limit(30)
        )
    ]

for name in MODELS:
    print("=" * 78)
    print(name)
    tokenizer = AutoTokenizer.from_pretrained(name)
    model = AutoModelForSequenceClassification.from_pretrained(name)
    model.eval()
    print("  id2label:", model.config.id2label)

    print("\n  controls:")
    control_probs = probs_for(model, tokenizer, [t for t, _ in CONTROLS])
    for (text, expected), probs in zip(CONTROLS, control_probs):
        top = max(range(len(probs)), key=lambda i: probs[i])
        print("    %-8s idx=%d [%s]  %s"
              % (expected, top, ", ".join("%.3f" % p for p in probs), text[:50]))

    corpus_probs = probs_for(model, tokenizer, corpus)
    tops = [max(range(len(p)), key=lambda i: p[i]) for p in corpus_probs]
    spread = [max(p) - min(p) for p in corpus_probs]

    print("\n  real corpus (%d headlines):" % len(corpus))
    print("    predicted index counts:",
          {i: tops.count(i) for i in sorted(set(tops))})
    print("    mean top-prob        : %.3f" % statistics.mean(max(p) for p in corpus_probs))
    print("    mean max-min spread  : %.3f" % statistics.mean(spread))
    # Per-class standard deviation across headlines: near zero means the model
    # is returning the same answer no matter what it is shown.
    per_class_sd = [
        statistics.pstdev([p[i] for p in corpus_probs]) for i in range(len(corpus_probs[0]))
    ]
    print("    per-class sd         :", ["%.4f" % s for s in per_class_sd])
    print()

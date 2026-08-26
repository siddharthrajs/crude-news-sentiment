"""Does flipping FinBERT's sign actually help?

Judged against headlines whose crude direction is not in doubt, drawn from the
live corpus plus controls for the cases inversion is expected to break.
"""

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from cns import scoring  # noqa: E402
from cns.config import settings  # noqa: E402

# (headline, true direction for crude, why)
CASES = [
    # Supply disruption / conflict -- FinBERT reads these negative, they are bullish.
    ("Indian oil tanker HAANA deterred after attempting to transit southern Strait of Hormuz Oman corridor following warning", +1, "transit blocked"),
    ("Shipper MSC halts Russian Black Sea service after drone attack", +1, "shipping halted"),
    ("OPEC announces deep cuts to crude production quotas", +1, "supply cut"),
    ("Saudi Arabia slashes oil output by two million barrels per day", +1, "supply cut"),
    ("Libya declares force majeure on crude exports after pipeline damage", +1, "exports halted"),
    ("US sanctions block Iranian crude exports entirely", +1, "exports blocked"),
    ("Iran is still in wartime situation, its nuclear sites have been damaged", +1, "war risk"),
    # De-escalation -- FinBERT reads these positive, they are bearish.
    ("Iran and Oman agree ceasefire terms over the Strait of Hormuz", -1, "risk unwinds"),
    ("Iran, Oman agreed on share of Hormuz revenues", -1, "risk unwinds"),
    ("Trump: The strait of Hormuz is functioning.", -1, "shipping normal"),
    # Supply increase -- FinBERT reads positive, bearish.
    ("OPEC raises production quotas sharply for next quarter", -1, "supply up"),
    ("Saudi Arabia to flood the market with extra crude supply", -1, "supply up"),
    # --- where inversion is expected to FAIL ---
    ("Global oil demand collapses as recession deepens", -1, "demand down"),
    ("China crude imports slump to a three-year low", -1, "demand down"),
    ("US crude inventories post a huge unexpected build", -1, "inventory build"),
    ("Oil slides 3% as the session closes weaker", -1, "price fell"),
    ("Brent crude tumbles below $60 a barrel", -1, "price fell"),
    ("Oil prices rally on strong demand outlook", +1, "price rose"),
]


def run(invert):
    settings.scorer_mode = "sentiment"
    settings.scorer_invert = invert
    out = []
    for text, truth, why in CASES:
        result = scoring.score(type("H", (), {"title": text})())
        got = 0 if result.direction == "neutral" else (1 if result.value > 0 else -1)
        out.append((text, truth, why, got, result.value))
    return out


plain = run(False)
inverted = run(True)

print("%-58s %-12s %8s %8s" % ("headline", "truth", "plain", "inverted"))
print("-" * 92)
plain_right = inv_right = 0
for (text, truth, why, g1, v1), (_, _, _, g2, v2) in zip(plain, inverted):
    plain_right += g1 == truth
    inv_right += g2 == truth
    mark = lambda ok: "OK " if ok else "XX "
    print("%-58s %-12s %s%+6.0f %s%+6.0f"
          % (text[:56], ("bullish" if truth > 0 else "bearish") + " (" + why[:0] + ")",
             mark(g1 == truth), v1, mark(g2 == truth), v2))

n = len(CASES)
print()
print("plain FinBERT : %d/%d correct (%.0f%%)" % (plain_right, n, plain_right / n * 100))
print("inverted      : %d/%d correct (%.0f%%)" % (inv_right, n, inv_right / n * 100))

supply = [c for c in zip(plain, inverted) if c[0][2] in
          ("transit blocked", "shipping halted", "supply cut", "exports halted",
           "exports blocked", "war risk", "risk unwinds", "shipping normal", "supply up")]
other = [c for c in zip(plain, inverted) if c not in supply]
print()
print("supply / risk headlines : plain %d/%d, inverted %d/%d"
      % (sum(p[3] == p[1] for p, _ in supply), len(supply),
         sum(i[3] == i[1] for _, i in supply), len(supply)))
print("demand / price headlines: plain %d/%d, inverted %d/%d"
      % (sum(p[3] == p[1] for p, _ in other), len(other),
         sum(i[3] == i[1] for _, i in other), len(other)))

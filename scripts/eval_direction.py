"""Grade the scorer's *direction* against the hand-read labels in tests/direction_labels.

Usage:
    python scripts/eval_direction.py            # headline accuracy + error table
    python scripts/eval_direction.py --quiet    # totals only

Two numbers matter and they trade off:

* **directional accuracy** over the headlines that genuinely have a direction.
  Getting these backwards is the expensive error -- it moves the index the
  wrong way.
* **weight carried by no-signal headlines.** The scorer deliberately calls a
  side on every headline -- "no opinion" is not a usable trading signal -- so
  the question is not whether it called one, but how loudly. A casualty count
  is allowed a direction; it is not allowed to move the index. This is graded
  as the share of total index weight sitting on headlines that have no
  direction to give.

`weighted error` combines them the way `market_index` does, by scoring each
mistake in proportion to the weight it would carry into the index
(|score| x confidence x salience). A wrong call at confidence 0.05 costs almost
nothing; a wrong call at 0.95 is the whole problem.
"""

import argparse
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

from direction_labels import LABELLED  # noqa: E402

from cns import scoring  # noqa: E402


class _H:
    """Minimal stand-in for a Headline row -- the scorer only reads `title`."""

    def __init__(self, title):
        self.title = title


def evaluate():
    rows = []
    for hid, title, truth in LABELLED:
        result = scoring.score(_H(title))
        if result is None:
            got, value, conf, sal = 0, 0.0, 0.0, 0.0
        else:
            got = {"bullish": 1, "bearish": -1, "neutral": 0}[result.direction]
            value = result.value
            conf = result.confidence
            sal = (result.components or {}).get("salience", 1.0)
        rows.append((hid, title, truth, got, value, conf, sal))
    return rows


def report(rows, quiet=False):
    directional = [r for r in rows if r[2] != 0]
    nosignal = [r for r in rows if r[2] == 0]

    right = [r for r in directional if r[3] == r[2]]
    flipped = [r for r in directional if r[3] != 0 and r[3] != r[2]]
    missed = [r for r in directional if r[3] == 0]
    false_signal = [r for r in nosignal if r[3] != 0]

    # What each mistake would actually contribute to the index.
    def weight(r):
        return abs(r[4]) / 100.0 * r[5] * r[6]

    err_weight = sum(weight(r) for r in flipped) + sum(weight(r) for r in nosignal)
    tot_weight = sum(weight(r) for r in rows) or 1.0

    if not quiet:
        for name, group in (
            ("SIGN FLIPPED (called the opposite of the truth)", flipped),
            ("FALSE SIGNAL (direction called on a no-signal headline)", false_signal),
            ("MISSED (real direction called neutral)", missed),
        ):
            print(f"\n--- {name}: {len(group)} ---")
            for hid, title, truth, got, value, conf, sal in sorted(
                group, key=lambda r: -abs(r[4]) * r[5] * r[6]
            ):
                name_of = {1: "bullish", -1: "bearish", 0: "no-signal"}
                print(
                    f"  {hid:>5} truth={name_of[truth]:<9} got={name_of[got]:<9}"
                    f"{value:>7.1f} conf={conf:.2f} sal={sal:.2f} | {title[:72]}"
                )

    print()
    print("=" * 72)
    print(f"corpus                    {len(rows)} headlines "
          f"({len(directional)} directional, {len(nosignal)} no-signal)")
    print(f"directional accuracy      {len(right)}/{len(directional)} "
          f"({len(right) / max(len(directional), 1):.0%})")
    print(f"  sign flipped            {len(flipped)}")
    print(f"  missed (called neutral) {len(missed)}")
    ns_weight = sum(weight(r) for r in nosignal)
    print(f"no-signal headlines       {len(nosignal)}, carrying "
          f"{ns_weight / tot_weight:.0%} of total index weight "
          f"(mean weight {ns_weight / max(len(nosignal), 1):.3f} vs "
          f"{sum(weight(r) for r in directional) / max(len(directional), 1):.3f} "
          f"for directional)")
    print(f"weighted error            {err_weight / tot_weight:.0%} "
          f"of total index weight comes from a mistake")
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    print("scorer version:", scoring.version())
    report(evaluate(), quiet=args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())

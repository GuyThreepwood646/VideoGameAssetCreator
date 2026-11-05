"""Mean metrics per experiment and condition from concepts.jsonl and styles.jsonl, as markdown tables."""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rlib  # noqa: E402


def load(name):
    p = os.path.join(rlib.OUT, name)
    rows = {}
    for line in open(p, encoding="utf-8"):
        r = json.loads(line)
        key = tuple(r.get(k) for k in ("exp", "style", "subject", "seed", "cond", "variant"))
        rows[key] = r  # last write wins (re-runs)
    return list(rows.values())


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def table(rows, group):
    g = collections.OrderedDict()
    for r in rows:
        g.setdefault(tuple(r[k] for k in group), []).append(r)
    print("| " + " | ".join(group) + " | n | min margin | single subject | bg std | arm gap (knight) | hip/shoulder (knight) | block err | colours |")
    print("|" + "---|" * (len(group) + 8))
    for k, rs in g.items():
        kn = [r for r in rs if r["subject"] == "knight"]
        print("| " + " | ".join(str(x) for x in k) + f" | {len(rs)} | {min(r['margin'] for r in rs):.3f} | "
              f"{sum(r['components'] == 1 for r in rs)}/{len(rs)} | {mean([r['bg_std'] for r in rs])} | "
              f"{mean([r.get('arm_gap_rows') for r in kn])} | {mean([r.get('hip_extent_over_shoulder') for r in kn])} | "
              f"{mean([r.get('block_err') for r in rs])} | {mean([r['colours_5bit'] for r in rs])} |")


if __name__ == "__main__":
    c = sorted(load("concepts.jsonl"), key=lambda r: (r["exp"], r["cond"]))
    table(c, ["exp", "cond"])
    print()
    s = sorted(load("styles.jsonl"), key=lambda r: (r["style"], r["variant"]))
    table(s, ["style", "variant", "model"])

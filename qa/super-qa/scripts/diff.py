#!/usr/bin/env python3
"""Longitudinal diff between two super-qa rounds.

Usage: diff.py <old-run> <new-run>     e.g. diff.py 2026-06-03 2026-09-15

Matches findings cross-round on:
  1. fingerprint (exact, fast path)
  2. fallback: same file + title-token Jaccard >= 0.5  (fingerprints change when the
     agent rewords a title with different content words — Jaccard catches those)

Reports: added (new in new-run), fixed (gone from old-run), persisted, severity_changed.
NOTE: round-1 (#181-233, ~3mo old) left GitHub issues but no consolidated.json, so the
first diffable baseline is round-2 (2026-06-03).
"""

import json
import re
import sys

STOP = {"the", "a", "an", "of", "in", "to", "and", "or", "is", "for", "on", "with"}


def load(run):
    return json.load(open(f"qa/super-qa/runs/{run}/consolidated.json"))


def toks(title):
    return {
        w
        for w in re.sub(r"[^a-z0-9]+", " ", title.lower()).split()
        if len(w) >= 3 and w not in STOP
    }


def file_of(loc):
    return (loc or "").split(":")[0]


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match(f, pool):
    # exact fingerprint
    for g in pool:
        if g.get("fingerprint") and g["fingerprint"] == f.get("fingerprint"):
            return g
    # fuzzy: same file + Jaccard >= 0.5
    ft = toks(f["title"])
    best, bs = None, 0.5
    for g in pool:
        if file_of(g["location"]) != file_of(f["location"]):
            continue
        j = jaccard(ft, toks(g["title"]))
        if j >= bs:
            best, bs = g, j
    return best


def main():
    if len(sys.argv) < 3:
        print("usage: diff.py <old-run> <new-run>", file=sys.stderr)
        sys.exit(1)
    old, new = load(sys.argv[1]), load(sys.argv[2])
    matched_old = set()
    added, persisted, sev_changed = [], [], []
    for f in new:
        m = match(f, old)
        if m is None:
            added.append(f)
        else:
            matched_old.add(id(m))
            persisted.append((f, m))
            if f["severity"] != m["severity"]:
                sev_changed.append((m["severity"], f["severity"], f["title"]))
    fixed = [g for g in old if id(g) not in matched_old]

    print(f"# super-qa diff {sys.argv[1]} -> {sys.argv[2]}\n")
    print(f"- old: {len(old)} | new: {len(new)}")
    print(
        f"- added (new): {len(added)} | fixed/gone: {len(fixed)} | persisted: {len(persisted)} "
        f"| severity changed: {len(sev_changed)}\n"
    )
    for label, items in (("ADDED", added), ("FIXED/GONE", fixed)):
        print(f"## {label} ({len(items)})")
        for f in sorted(items, key=lambda x: x["severity"]):
            print(f"- [{f['severity']}] {f['location']} — {f['title'][:70]}")
        print()
    if sev_changed:
        print(f"## SEVERITY CHANGED ({len(sev_changed)})")
        for o, n, t in sev_changed:
            print(f"- {o} -> {n}: {t[:70]}")


if __name__ == "__main__":
    main()

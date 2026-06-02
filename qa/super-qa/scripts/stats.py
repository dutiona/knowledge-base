#!/usr/bin/env python3
"""consolidated.json -> stats.md + summary.json (trend-diffable).

Usage: stats.py [RUN] [reference_commit]
summary.json is the small artifact future rounds diff for severity/area/category trends.
"""

import json
import subprocess
import sys
from collections import Counter

RUN = sys.argv[1] if len(sys.argv) > 1 else "2026-06-03"
RUNDIR = f"qa/super-qa/runs/{RUN}"
fs = json.load(open(f"{RUNDIR}/consolidated.json"))

try:
    ref = (
        sys.argv[2]
        if len(sys.argv) > 2
        else subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    )
except Exception:
    ref = "unknown"

SEVS = ["critical", "high", "medium", "low", "info"]
sev = Counter(f["severity"] for f in fs)
cat = Counter(f["category"] for f in fs)
area = Counter(f["_area"] for f in fs)
typ = Counter(f.get("_type", "?") for f in fs)
af = sum(1 for f in fs if f.get("auto_fixable"))
af_sev = Counter(f["severity"] for f in fs if f.get("auto_fixable"))
verified = sum(1 for f in fs if f.get("_verified"))
fallback = sum(1 for f in fs if f.get("fallback_used"))
debated = sum(1 for f in fs if f.get("_debate"))

# module-level (file basename)
mod = Counter(
    (f["location"].split(":")[0].replace("src/knowledge_base/", "")) for f in fs
)

L = [
    f"# super-qa stats — round {RUN}",
    "",
    f"- reference commit: `{ref}`",
    f"- total findings: **{len(fs)}** | auto-fixable: **{af}** | "
    f"verified(static/debate): **{verified + debated}** | security-fallback: **{fallback}**",
    "",
    "## Severity × auto-fix",
    "",
    "| severity | total | auto-fix | report-only |",
    "| --- | --- | --- | --- |",
]
for s in SEVS:
    L.append(
        f"| {s} | {sev.get(s, 0)} | {af_sev.get(s, 0)} | {sev.get(s, 0) - af_sev.get(s, 0)} |"
    )
L.append(f"| **total** | **{len(fs)}** | **{af}** | **{len(fs) - af}** |")

L += ["", "## By category", "", "| category | n |", "| --- | --- |"]
L += [f"| {k} | {v} |" for k, v in cat.most_common()]
L += ["", "## By area", "", "| area | n |", "| --- | --- |"]
L += [f"| {k} | {v} |" for k, v in area.most_common()]
L += ["", "## By type (label)", "", "| type | n |", "| --- | --- |"]
L += [f"| {k} | {v} |" for k, v in typ.most_common()]
L += ["", "## By module", "", "| module | n |", "| --- | --- |"]
L += [f"| {k} | {v} |" for k, v in mod.most_common()]

open(f"{RUNDIR}/stats.md", "w").write("\n".join(L) + "\n")

summary = {
    "run": RUN,
    "tool": "super-qa round 2 (full 5-lens, 30 agents + static + debate)",
    "language": "python",
    "reference_commit": ref,
    "units_audited": 6,
    "agents": 30,
    "findings_total": len(fs),
    "by_severity": {s: sev.get(s, 0) for s in SEVS},
    "by_category": dict(cat),
    "by_area": dict(area),
    "by_type": dict(typ),
    "auto_fixable": af,
    "verified_or_debated": verified + debated,
    "security_fallback": fallback,
    "debated": debated,
    "supply_chain": {"pip_audit_cve_packages": 6},
}
json.dump(summary, open(f"{RUNDIR}/summary.json", "w"), indent=2)
print(f"wrote {RUNDIR}/stats.md and summary.json (ref {ref[:10]})")
print(
    "severity:",
    dict(sev),
    "| auto-fix:",
    af,
    "| debated:",
    debated,
    "| fallback:",
    fallback,
)

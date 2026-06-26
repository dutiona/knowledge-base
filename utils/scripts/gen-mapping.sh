#!/usr/bin/env bash
# gen-mapping.sh — READ-ONLY. Generate utils/scripts/mapping.tsv: one row per OPEN
# issue with proposed type:/area:/cross-cutting/Phase, using ROADMAP.md as the
# oracle and live gh labels. Rows needing human judgement are flagged ⚠ in the
# needs_review column. Closed issues are NOT touched.
#
# Output columns (TSV):
#   number  type  area  xcut  phase  needs_review  title  current_labels
#     xcut = the cross-cutting label to add (priority:* for planning issues,
#            severity:* for super-qa findings), or empty.
#
# Usage: utils/scripts/gen-mapping.sh   (writes utils/scripts/mapping.tsv; prints summary)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/pm-common.sh
source "$HERE/lib/pm-common.sh"
preflight

ROADMAP="$HERE/../ROADMAP.md"
OUT="$HERE/mapping.tsv"
ISSUES_JSON="$(mktemp)"
trap 'rm -f "$ISSUES_JSON"' EXIT

log "fetching open issues…"
gh issue list -R "$SLUG" --state open --limit 300 \
	--json number,title,labels >"$ISSUES_JSON"

OUT="$OUT" ROADMAP="$ROADMAP" ISSUES_JSON="$ISSUES_JSON" python3 - <<'PY'
import json, os, re

roadmap = os.environ["ROADMAP"]
issues  = json.load(open(os.environ["ISSUES_JSON"]))
out     = os.environ["OUT"]

# --- 1. parse ROADMAP issue-index table: | # | Title | Workstream | Phase | ... ---
rm = {}  # num -> (workstream, phase)
row_re = re.compile(r'^\|\s*(\d+)\s*\|(.+)$')
for line in open(roadmap, encoding="utf-8"):
    m = row_re.match(line.strip())
    if not m:
        continue
    num = int(m.group(1))
    cols = [c.strip() for c in m.group(2).split('|')]
    # cols: [title, workstream, phase, status, ...] (index table has these)
    if len(cols) >= 3:
        ws, ph = cols[1], cols[2]
        # only accept plausible workstream/phase cells (skip other tables)
        if ws and ph and len(ws) < 20 and len(ph) < 6:
            rm[num] = (ws, ph)

WS2AREA = {
    "Embedding": "embeddings", "Extraction": "extraction", "Ingest": "ingest",
    "Search": "search", "Papers": "papers", "Vision": "vision",
    "Integration": "integration", "Scale": "infra",
    # Foundation + Mixed resolved heuristically below
}
PHASE_OK = {"2.5c","3A","3B","3C","3D","3E","3F","3G","3H","3I","4","4+","Deferred"}

PREFIX2TYPE = {
    "feat":"feature","fix":"bug","perf":"perf","eval":"eval","research":"research",
    "design":"research","refactor":"refactor","docs":"docs","chore":"chore",
    "epic":"epic","test":"test",
}
prefix_re = re.compile(r'^([a-z]+)(\([^)]*\))?:', re.I)
# super-qa issues are titled "[super-qa] <severity>: <desc>" (or "[super-qa] <kind> (N)")
superqa_re = re.compile(r'^\[super-qa\]\s*(critical|high|medium|low|info)?\b', re.I)
# landscape-gap issues titled "KB-P<n>-<letter>: <desc>" carry a deliberate priority.
kbpx_re = re.compile(r'^KB-P([0-4])-[A-Z]\b', re.I)
# P0=critical(blocker), P1=high, P2=medium, P3/P4=low (low+info folded into low).
KBPX_PRIORITY = {"0":"critical","1":"high","2":"medium","3":"low","4":"low"}

def classify_superqa(title):
    """Infer type from a super-qa finding's description keywords."""
    t = title.lower()
    if any(k in t for k in ("n+1","o(p","o(n","o(e","unbatched","round-trip",
                            "full table scan","uncompiled regex","fetches all","per-paper")):
        return "perf"
    if any(k in t for k in ("no tests","zero tests","untested","no docstring",
                            "docstrings","missing from","phantom","docs gap")):
        # docs vs test: "tests" → test, else docs
        return "test" if ("test" in t or "untested" in t) else "docs"
    if any(k in t for k in ("coupling","god module","wet code","duplicat","primitive obsession",
                            "consolidate","fragile pattern","f-string sql","owns relationship",
                            "inline sql")):
        return "refactor"
    if any(k in t for k in ("injection","ssrf","auth","secret","sanitiz","validation")):
        return "security"
    return "enhancement"

def derive_type(title, labels):
    m = prefix_re.match(title)
    if m and m.group(1).lower() in PREFIX2TYPE:
        return PREFIX2TYPE[m.group(1).lower()], False
    sq = superqa_re.match(title)
    if sq or "super-qa" in labels:
        typ = classify_superqa(title)
        # confident on perf/test/docs/refactor/security keyword hits; flag only the
        # fallthrough 'enhancement' (no keyword matched → genuinely ambiguous).
        return typ, (typ == "enhancement")
    # bracket-prefixed issues: "[security] ...", "[extraction] ..." etc.
    b = re.match(r'^\[([a-z-]+)\]\s*(.*)', title, re.I)
    if b:
        tag, rest = b.group(1).lower(), b.group(2).lower()
        if tag == "security" or "injection" in rest or "ssrf" in rest:
            return "security", False
        # otherwise fall through using the remaining text for keyword hints
    # KB-Px landscape-gap issues are deliberate work items — classify by keywords,
    # not flagged as ambiguous (their priority is set from the P-number elsewhere).
    if kbpx_re.match(title):
        t = title.lower()
        if any(k in t for k in ("benchmark","evaluate","eval ")): return "eval", False
        if any(k in t for k in ("document","positioning","docs")): return "docs", False
        if any(k in t for k in ("research","structural-hash","adapters")): return "research", False
        return "feature", False
    if "bug" in labels:        return "bug", False
    if "documentation" in labels: return "docs", False
    if "refactoring" in labels: return "refactor", False
    if "research" in labels:   return "research", False
    if "security" in labels:   return "security", False
    if "enhancement" in labels: return "enhancement", True   # feature|enhancement — judge
    return "enhancement", True                                # default + flag

def derive_area(num, title, labels):
    if "database" in labels: return "db", False
    if "retrieval" in labels: return "search", False
    ws = rm.get(num, (None,None))[0]
    if ws in WS2AREA: return WS2AREA[ws], False
    t = title.lower()
    if ws == "Foundation":
        if any(k in t for k in ("server","mcp","route","tool ")): return "mcp", True
        if any(k in t for k in ("schema","sql","db","sqlite","migration")): return "db", True
        if any(k in t for k in ("doc","readme","architecture","cognitive")): return "docs", True
        return "infra", True   # CI/build/indexer/serve/scaling/package — judge
    if ws == "Mixed" or ws is None:
        return "infra", True
    return "infra", True

def derive_xcut(num, title, labels):
    # super-qa findings carry an intrinsic severity — from the bare high/med/low/
    # info label OR the "[super-qa] <severity>:" title prefix. Everything else:
    # priority is a forward-looking SCHEDULING decision the user makes on the
    # board — NOT derivable from labels, so a blank priority is EXPECTED, never
    # a review flag.
    sev = next((l for l in ("critical","high","medium","low","info") if l in labels), None)
    sq = superqa_re.match(title)
    if sq or "super-qa" in labels:
        tsev = sq.group(1).lower() if (sq and sq.group(1)) else None
        eff = sev or tsev
        return (f"severity:{eff}" if eff else "severity:medium", bool(not eff))
    kb = kbpx_re.match(title)
    if kb:
        return (f"priority:{KBPX_PRIORITY[kb.group(1)]}", False)  # deliberate P0-P4 priority
    if sev in ("high","medium","low"):
        return (f"priority:{sev}", False)   # bare high/med/low outside super-qa → priority
    return ("", False)  # no signal → leave blank, NOT a flag

def derive_phase(num):
    # Only phases in PHASE_OK are valid open-work buckets. The ROADMAP index table
    # also lists completed Phase 0/1/2/2.5a/2.5b rows; an OPEN issue mapping to one
    # of those means the roadmap is stale for it → blank phase + flag, don't emit
    # an invalid option the board can't accept.
    ph = rm.get(num, (None,None))[1]
    if not ph: return ("", True)
    ph = ph.replace("3C+","3C")
    if ph in PHASE_OK: return (ph, False)
    return ("", True)  # phase present but not an open-work bucket (e.g. 0/1/2/2.5a) → review

rows = []
for it in issues:
    num = it["number"]; title = it["title"]
    labels = [l["name"] for l in it["labels"]]
    typ, t_flag = derive_type(title, labels)
    area, a_flag = derive_area(num, title, labels)
    xcut, x_flag = derive_xcut(num, title, labels)
    phase, p_flag = derive_phase(num)
    flags = []
    if t_flag: flags.append("type")     # enhancement→feature? ambiguous
    if a_flag: flags.append("area")     # guessed area (Foundation/Mixed split)
    if x_flag: flags.append("sev")      # super-qa issue missing a severity signal
    if p_flag: flags.append("phase")    # no valid open-work phase in roadmap
    if num not in rm: flags.append("not-in-roadmap")
    nr = "⚠:" + ",".join(flags) if flags else ""
    rows.append((num, f"type:{typ}", f"area:{area}", xcut, phase, nr,
                 title.replace("\t"," "), ";".join(labels)))

rows.sort()
with open(out, "w", encoding="utf-8") as f:
    f.write("number\ttype\tarea\txcut\tphase\tneeds_review\ttitle\tcurrent_labels\n")
    for r in rows:
        f.write("\t".join(str(x) for x in r) + "\n")

flagged = sum(1 for r in rows if r[5])
print(f"WROTE {out}: {len(rows)} open issues, {flagged} flagged ⚠ for review", )
# quick phase histogram
from collections import Counter
ph = Counter(r[4] or "(none)" for r in rows)
print("phase dist:", dict(sorted(ph.items())))
ar = Counter(r[2] for r in rows)
print("area dist :", dict(sorted(ar.items())))
PY

ok "gen-mapping complete → $OUT (review the ⚠ rows before migrate-issues.sh)"

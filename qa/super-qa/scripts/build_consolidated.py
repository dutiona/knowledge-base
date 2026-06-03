#!/usr/bin/env python3
"""Build the canonical consolidated.json for a super-qa round.

Inputs (this round, round 2 / 2026-06-03):
  - /tmp/qa_llm_findings.json  : 161 LLM lens findings (from the 30-agent workflow,
                                 each tagged _unit/_lens by qa_aggregate.py)
  - hardcoded STATIC[]         : net-new deterministic-tool findings (pip-audit, mypy,
                                 build hygiene) verified by the orchestrator
  - debate verdicts            : applied to the two net-new search findings

Output:
  qa/super-qa/runs/<RUN>/consolidated.json  — canonical finding archive with
  fingerprint (stable cross-round key), _area, _type, _debate (structured),
  _verified, auto_fixable. This is THE artifact round 3 diffs against.

Fingerprint = sha256(module | category | normalized_title)[:12], normalized so a
trivial agent rewording (line numbers, articles, casing, counts) does NOT change it.
"""

import hashlib
import json
import re
import sys

RUN = "2026-06-03"
SRC = "/tmp/qa_llm_findings.json"
OUT = f"qa/super-qa/runs/{RUN}/consolidated.json"

# unit -> subsystem area (CLAUDE.md area: taxonomy). web.py+render_page are web
# ingestion -> ingest.
AREA = {
    "A-web": "ingest",
    "B-vision": "vision",
    "C-ingest": "ingest",
    "D-extraction": "extraction",
    "E1-db": "db",
    "E2-search": "search",
}

# finding category -> issue type: label (CLAUDE.md type: taxonomy)
TYPE = {
    "correctness": "bug",
    "security": "security",
    "performance": "perf",
    "testing": "test",
    "documentation": "docs",
    "design": "refactor",
    "refactoring": "refactor",
    "style": "chore",
    "type-safety": "bug",
    "supply-chain": "security",
    "infra": "chore",
}


def fingerprint(module: str, category: str, title: str) -> str:
    """Stable cross-round key. module = file path w/o line numbers."""
    t = title.lower()
    t = re.sub(r"\d+", "", t)  # strip line numbers / counts
    t = re.sub(r"\b(the|a|an|of|in|to)\b", " ", t)  # drop low-signal words
    t = re.sub(r"[^a-z]+", " ", t).strip()  # keep letters only
    t = re.sub(r"\s+", " ", t)
    key = f"{module}|{category}|{t}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def module_of(location: str) -> str:
    return (location or "").split(":")[0].strip()


# ---- net-new deterministic-tool findings (not in the LLM JSON) ----
CVE_TABLE = (
    "pip-audit (28 deps audited) — known CVEs:\n"
    "| package | version | advisory | fix |\n|---|---|---|---|\n"
    "| idna | 3.11 | CVE-2026-45409 | 3.15 |\n"
    "| pip | 26.0.1 | CVE-2026-3219 | 26.1 |\n"
    "| pip | 26.0.1 | CVE-2026-6357 | 26.1 |\n"
    "| pygments | 2.19.2 | CVE-2026-4539 | 2.20.0 |\n"
    "| requests | 2.32.5 | CVE-2026-25645 | 2.33.0 |\n"
    "| urllib3 | 2.6.3 | PYSEC-2026-142 | 2.7.0 |\n"
    "| urllib3 | 2.6.3 | PYSEC-2026-141 | 2.7.0 |\n"
    "requests/urllib3/idna are transitive (trafilatura) on web.py's HTTP/SSRF fetch path."
)
STATIC = [
    {
        "id": "infra/supply-chain-cves",
        "severity": "medium",
        "category": "supply-chain",
        "location": "pyproject.toml / uv.lock",
        "auto_fixable": True,
        "title": "6 dependencies with known CVEs (requests/urllib3/idna/pygments/pip)",
        "description": CVE_TABLE,
        "suggested_fix": "Bump fixed versions: idna>=3.15, pygments>=2.20.0, requests>=2.33.0, "
        "urllib3>=2.7.0; upgrade pip>=26.1 in CI image. Then `uv lock` + verify.",
        "references": [
            "CVE-2026-45409",
            "CVE-2026-3219",
            "CVE-2026-6357",
            "CVE-2026-4539",
            "CVE-2026-25645",
            "PYSEC-2026-142",
            "PYSEC-2026-141",
        ],
        "_verified": "pip-audit 2.10.0",
        "_lens": "static:pip-audit",
    },
    {
        "id": "infra/build-no-tool-config",
        "severity": "medium",
        "category": "infra",
        "location": "pyproject.toml",
        "auto_fixable": True,
        "title": "No [tool.ruff]/[tool.mypy]/[tool.pyright] config — lint runs default ruleset only",
        "description": "pyproject.toml has no [tool.ruff], [tool.mypy], or [tool.pyright] section "
        "despite ruff/mypy/pyright all installed. Ruff therefore runs its DEFAULT "
        "minimal ruleset (E/F/W) — the Phase-1 '0 warnings' undersells real lint debt — "
        "and there is no enforced type checking (20 mypy errors exist today).",
        "suggested_fix": "Add [tool.ruff.lint] with a real select (E,F,W,B,SIM,UP,C4,PTH,RUF,S) and "
        "[tool.mypy] (or pyright config) + a CI gate. Triage the surfaced findings.",
        "references": [],
        "_verified": "grep pyproject.toml",
        "_lens": "static:hygiene",
    },
    {
        "id": "infra/build-unpinned-upper-bounds",
        "severity": "low",
        "category": "infra",
        "location": "pyproject.toml",
        "auto_fixable": False,
        "title": "Direct deps use >= without upper bounds",
        "description": "httpx/numpy/pillow/sqlite-vec/trafilatura/... are pinned only with >= (no "
        "upper bound). Mitigated by uv.lock (present, 390KB) which pins exact versions "
        "for reproducible installs, so impact is low; flagged for SemVer-break awareness.",
        "suggested_fix": "Optionally add conservative upper bounds (e.g. <next-major) on load-bearing "
        "deps; or document that uv.lock is the reproducibility contract.",
        "references": [],
        "_verified": "grep pyproject.toml",
        "_lens": "static:hygiene",
    },
    {
        "id": "extraction/typesafety-defaultdict-union",
        "severity": "low",
        "category": "type-safety",
        "location": "src/knowledge_base/extraction.py:582-648",
        "auto_fixable": False,
        "title": "Loosely-typed defaultdict yields 11 mypy union-attr false positives",
        "description": "The entity_data defaultdict factory returns a heterogeneous dict "
        "({'type':None,...,'mentions':[]}), so mypy widens the value type to list|None "
        "and flags .append/__iter__ on data['mentions'] (lines 607/629/648). This is "
        "NOT a runtime bug — mentions is always [] from the factory — but it is real "
        "type-debt that masks genuine None errors. Verified by reading the code.",
        "suggested_fix": "Define a TypedDict (e.g. _EntityData with mentions: list[_Mention]) and type "
        "the defaultdict factory, so mypy tracks per-key types.",
        "references": [],
        "_verified": "mypy 1.19.1 + source read",
        "_lens": "static:mypy",
    },
    {
        "id": "search/typesafety-no-redef-valid-ids",
        "severity": "low",
        "category": "type-safety",
        "location": "src/knowledge_base/search.py:395",
        "auto_fixable": False,
        "title": "valid_ids redefined (mypy no-redef) — shadows line 340",
        "description": "mypy [no-redef]: valid_ids is annotated/defined at line 340 (chunk_strategy "
        "pre-filter) and re-declared at 395 (rerank pre-filter). Benign today (disjoint "
        "scopes) but the shadow is a readability/typing smell; verify no logic overlap.",
        "suggested_fix": "Rename one (e.g. rerank_valid_ids) so the two filter passes don't share a name.",
        "references": [],
        "_verified": "mypy 1.19.1",
        "_lens": "static:mypy",
    },
    {
        "id": "ingest/typesafety-no-redef-page-map",
        "severity": "low",
        "category": "type-safety",
        "location": "src/knowledge_base/ingest.py:302",
        "auto_fixable": False,
        "title": "page_map redefined (mypy no-redef) — shadows line 299",
        "description": "mypy [no-redef]: page_map defined at 299 then re-declared at 302. Likely a "
        "conditional-branch reassignment mypy rejects; verify both branches intend the "
        "same variable.",
        "suggested_fix": "Unify the two assignments or rename; annotate once.",
        "references": [],
        "_verified": "mypy 1.19.1",
        "_lens": "static:mypy",
    },
]

# structured debate verdicts (coraly schema) for the two net-new search findings
DEBATE = {
    "src/knowledge_base/search.py:402-422": {
        "original": "high",
        "gemini": "high",
        "codex": "medium",
        "verdict": "high",
        "synthesis": "Opus HIGH: reranker[0,1] vs RRF(~0.016) co-sort; triggers at default top_k "
        "when source_type/chunk_strategy filter set; compounds with under-fetch; "
        "default unfiltered path unaffected (Codex caveat).",
    },
    "src/knowledge_base/search.py:430-450": {
        "original": "high",
        "gemini": "medium",
        "codex": "medium",
        "verdict": "medium",
        "synthesis": "Both models MEDIUM: source_type applied only at final SQL fetch; 3x overfetch "
        "softens but architecturally inconsistent with chunk_strategy pre-filter.",
    },
}


def main():
    fs = json.load(open(SRC))
    for f in fs:
        loc = f["location"]
        # exact debate overrides (specialist lens only — avoid prefix collisions)
        if loc in DEBATE and f.get("_lens") == "specialist":
            f["_debate"] = DEBATE[loc]
            f["severity"] = DEBATE[loc]["verdict"]
        f["_area"] = AREA.get(f.get("_unit"), "infra")
        f["_type"] = TYPE.get(f["category"], "refactor")
        f["_verified"] = None
        f["fingerprint"] = fingerprint(module_of(loc), f["category"], f["title"])
    for s in STATIC:
        s["_unit"] = "static"
        s.setdefault("_area", s["id"].split("/")[0])
        s["_type"] = TYPE.get(s["category"], "chore")
        s["fingerprint"] = fingerprint(
            module_of(s["location"]), s["category"], s["title"]
        )
    allf = fs + STATIC

    # fingerprint collision check
    seen = {}
    coll = 0
    for f in allf:
        if f["fingerprint"] in seen:
            coll += 1
            print(
                f"  COLLISION {f['fingerprint']}: {f['id']} <-> {seen[f['fingerprint']]}",
                file=sys.stderr,
            )
        else:
            seen[f["fingerprint"]] = f["id"]

    json.dump(allf, open(OUT, "w"), indent=2)
    from collections import Counter

    sev = Counter(f["severity"] for f in allf)
    print(
        f"wrote {OUT}: {len(allf)} findings ({len(fs)} LLM + {len(STATIC)} static), "
        f"{coll} fingerprint collisions"
    )
    print(
        "severity:",
        {k: sev.get(k, 0) for k in ("critical", "high", "medium", "low", "info")},
    )
    print("areas:", dict(Counter(f["_area"] for f in allf)))


if __name__ == "__main__":
    main()

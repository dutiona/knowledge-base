# super-qa — knowledge-base

Durable infrastructure for `/super-qa` rounds: canonical findings, idempotent GitHub
issue filing, cross-round diffs, and trend stats. Built so a round 3–6 months out can
re-run, dedupe against this round, and chart severity trends.

## Layout

```
qa/super-qa/
├── README.md            this file (conventions + re-run guide)
├── dupe_map.json        curated: my-finding fingerprint -> prior-round issue # (carry-forward)
├── scripts/
│   ├── build_consolidated.py   /tmp LLM findings + static + debate -> runs/<RUN>/consolidated.json
│   ├── gen_manifest.py         consolidated.json + dupe_map -> runs/<RUN>/issue_manifest.json
│   ├── file_issues.sh          idempotent gh filer (DRY=1 to preview); phases below
│   ├── stats.py                consolidated.json -> stats.md + summary.json (trend-diffable)
│   └── diff.py                  cross-round diff (fingerprint + Jaccard fallback)
└── runs/<RUN>/          one dir per round (RUN = YYYY-MM-DD)
    ├── consolidated.json        CANONICAL finding archive (fingerprinted) — diff target
    ├── findings.md              human narrative (exec summary, debate, refactor backlog)
    ├── summary.json             small metrics blob — `git diff` across rounds for trends
    ├── issue_manifest.json      issue specs (epics/findings/buckets/autofix/observers)
    ├── issue_state.json         ledger: manifest-key -> {number,node} (idempotency + traceability)
    ├── stats.md                 severity×autofix / category / area / type / module tables
    └── raw-workflow-output.json.gz   archived raw N-agent output (repro)
```

## Issue structure (round 2, 2026-06-03)

```
MASTER epic
 ├─ area epic ×N (db, extraction, infra, ingest, mcp, papers, search, vision)
 │    ├─ individual issue   — one per NON-autofixable critical/high/medium finding
 │    ├─ low/info bucket     — one per area (grouped non-autofixable low+info)
 │    └─ (retrofitted prior-round issues, relabeled + linked)
 ├─ auto-fix epic
 │    └─ auto-fix bucket ×severity — ALL auto-fixable findings, one issue per severity
 └─ observer ×severity      — #-listing index of all non-autofixable findings per severity
```

## Labelling

Every issue: `super-qa` + `severity:{critical|high|medium|low|info}` + `area:*` +
`type:{bug|security|test|docs|refactor|perf|chore|epic}`. Epics add `type:epic`.
Auto-fix issues add `super-qa:auto-fix`. Security-lens findings that ran below
`effort: max` add `super-qa:security-fallback`. `priority:` is left for manual board triage.

## Cross-round keys

- **fingerprint** = `sha256(module | category | normalized_title)[:12]` (in `consolidated.json`).
  Stable under casing/line-number/article churn; **changes when the agent rewords a title
  with different content words** — that's why `diff.py` also does file + token-Jaccard ≥ 0.5.
- **dupe_map.json** is keyed by fingerprint and **hand-curated each round** (the heuristic
  matcher under-matches). Round 2 linked 6 findings to prior issues #194/#207/#208/#210/#213.

## Re-running (round N → N+1)

1. Run `/super-qa` full dispatch; let it produce LLM findings + static-tool results.
2. `python3 scripts/build_consolidated.py` (edit RUN + the STATIC[]/DEBATE blocks for the new round)
   → `runs/<NEW>/consolidated.json`.
3. `python3 scripts/diff.py <prev> <NEW>` → see added / fixed / persisted / severity-changed.
4. Curate `dupe_map.json`: for each persisted finding that maps to an existing open issue,
   add `"<fingerprint>": {"issue": <#>, "note": "..."}`.
5. `python3 scripts/gen_manifest.py` → `issue_manifest.json`.
6. `DRY=1 ./scripts/file_issues.sh all` → review every action.
7. `./scripts/file_issues.sh all` → file + relabel + link (idempotent; safe to re-run).
8. `python3 scripts/stats.py` → refresh stats.md + summary.json. Commit `runs/<NEW>/`.

`file_issues.sh` phases: `labels epics findings buckets autofix observers retrofit` (or `all`).

## Methodology & trust

- 30-agent fan-out surfaces _candidates_; the security lens ran at tier-2 fallback
  (`octo:personas:security-auditor`, `xhigh` not `max`) — `qa-security` isn't dispatchable
  here. Those findings carry `super-qa:security-fallback` and a body caveat.
- Deterministic tools (bandit/pip-audit/mypy) were run by the orchestrator and cross-checked
  against the LLM lenses (e.g. 18 bandit B608 SQL hits were adjudicated as safe parameterized
  identifier interpolation). Their findings are marked `_verified`.
- Two net-new search-correctness findings were settled by Gemini+Codex debate; `_debate`
  records original/gemini/codex/verdict/synthesis in `consolidated.json`.
- Non-verified issue bodies carry a "review before action" header; verified/debated ones a ✅.

## Gotchas (carry forward)

1. **Round 1 (#181–233, ~Mar 2026) left issues but no `consolidated.json`** — the first
   diffable baseline is round 2. Future rounds always diff against the prior `consolidated.json`.
2. **`issue_state.json` is the resume ledger.** Delete an entry to force re-file that key.
   Lost entirely? The filer falls back to title-match against all super-qa issues.
3. **`addSubIssue` needs GraphQL node IDs**, not issue numbers — the ledger stores both.
4. Line numbers in `location`/`findings.md` are point-in-time; never use them as keys.

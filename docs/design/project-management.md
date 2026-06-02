# Knowledge-Base Project Management Design

> **Status: PROPOSAL — awaiting final validation.** Adapted from `dutiona/reify`
> and `Corely-Cycle/coraly-cycle`, tailored to knowledge-base's ROADMAP and
> module structure. Once validated, a `/super-plan` turns this into actionable
> work.
>
> Scope (per intake): full system (labels + Projects + fields + automation +
> docs), migrate **open issues only** (closed stay as-is), new issues onward.

## Decisions locked (from validation round 1)

| Decision             | Choice                                                                              |
| -------------------- | ----------------------------------------------------------------------------------- |
| **Priority scale**   | `critical` / `high` / `medium` / `low` (reify-style words) — label **and** field    |
| **Deferred**         | **NOT a priority** — it is a **value of the Phase field** (parking lot lives there) |
| **Phase**            | a Project **field** (single-select), not a label                                    |
| **Areas**            | 11 subsystem areas (Foundation grab-bag split into db/mcp/infra/docs)               |
| **Status column**    | add **`In Review`** (PR-open stage)                                                 |
| **4th board**        | **yes** — `KB — Research & Eval`                                                    |
| **CI**               | **yes** — minimal `ruff` + `pytest` workflow                                        |
| **`type:perf/eval`** | **yes** — both added                                                                |
| **Migration**        | **system-first + reviewed batch** (stand up system, then approve a mapping table)   |

---

## 0. Why (the gap)

KB's current labels are an unstructured pile: the default GitHub set
(`bug`, `enhancement`, `documentation`, `duplicate`, `help wanted`, …) plus
ad-hoc bare words with **no dimension prefix** — `high`/`medium`/`low`,
`info`, `retrieval`, `database`, `quality`, `refactoring`, `research`,
`security`, `super-qa`, `plan`. There is **no `type:`/`area:`/`priority:`
convention**, **no GitHub Project**, **no milestones**, and **zero
`.github/workflows`** (no CI, no automation).

Both reference repos converge on a disciplined **`prefix:value`** taxonomy with
two required axes (`type:`, `area:`) and additive cross-cutting labels, fed into
auto-populated GitHub Projects. This document adapts that to KB.

**Verified reference facts** (from `gh label list`, `gh project field-list`,
`gh project list`, and the repos' `CLAUDE.md` + `add-to-project.yml`):

| Aspect         | reify (`dutiona`)                                                           | coraly (`Corely-Cycle`)                                              |
| -------------- | --------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Required axes  | `type:` + `area:`                                                           | `type:` + `area:`                                                    |
| `priority:`    | critical/high/medium/low (labels = source of truth)                         | critical/high **only** (labels) + Project **Priority** field (P0–P4) |
| Extra axes     | `tier:0-3`, `status:blocked/parked/needs-design`, `severity:*` (super-qa)   | `severity:*`, domain: `rgpd`/`partnership`/`demo`/`deploy:prod`      |
| Project fields | **default only** (Status: Todo/In Progress/Done) — _labels-only model_      | Main adds **Priority(P0–P4)** + **Phase(0–4)** (undocumented in doc) |
| Projects       | Main(2) / Critical-Path(3) / Bug&Security-Triage(1)                         | Main(2) / Triage(3) / V1(1) / Ederra(4)                              |
| Automation     | `add-to-project.yml`: auto-add Main; +Triage if `type:bug\|\|type:security` | auto-add Main + V1 (unconditional)                                   |
| Critical Path  | **manually curated** (no auto-job)                                          | manual                                                               |
| Title format   | `type(scope): description`                                                  | `type(area): description`                                            |
| Config files   | CLAUDE/AGENTS/GEMINI **byte-identical**                                     | byte-identical                                                       |

**Adapt + improve decisions** (where the references disagree or fall short):

- **Priority = words, mirrored as label + field.** Take reify's
  `critical/high/medium/low` _scale_, but also expose it as a Project **field**
  (coraly proved the field is worth it for sort/group). Same values both sides,
  no translation — fixing coraly's `critical/high` label vs `P0–P4` field
  mismatch.
- **Phase = a field with KB's _real_ subphases** (2.5c, 3A–3I, 4, 4+) plus a
  **`Deferred`** value for the parking lot — richer than coraly's flat Phase 0–4,
  which it left undocumented.
- **Document everything** the references left implicit (the Priority/Phase
  fields, the triage rules) in KB's `CLAUDE.md`.

---

## 1. Label taxonomy

Convention: **`prefix:value`**, lowercase, colon-separated. Two **required**
axes per issue (`type:` + `area:`); the rest additive.

### 1.1 `type:` — what kind of work (exactly one, required)

| Label              | Use                                                     | Title prefix          |
| ------------------ | ------------------------------------------------------- | --------------------- |
| `type:bug`         | Something is broken                                     | `fix:`                |
| `type:feature`     | New capability                                          | `feat:`               |
| `type:enhancement` | Improve existing functionality                          | `feat:`/`improve`     |
| `type:perf`        | Performance fix (N+1, O(n²), batching)                  | `perf:`               |
| `type:refactor`    | Restructure, no behavior change                         | `refactor:`           |
| `type:test`        | Test coverage / infra                                   | `test:`               |
| `type:docs`        | Documentation only                                      | `docs:`               |
| `type:chore`       | Deps, config, tooling, cleanup                          | `chore:`              |
| `type:research`    | Spike / design / PoC (deliverable: ADR or note)         | `research:`/`design:` |
| `type:eval`        | Benchmark an existing tool/model (deliverable: numbers) | `eval:`               |
| `type:security`    | Security finding or hardening                           | `fix:`/`feat:`        |
| `type:epic`        | Umbrella with sub-issues                                | `epic:`               |
| `type:plan`        | Implementation-plan reference (links to PR)             | `plan:`               |

> **KB additions vs reify:** `type:perf` and `type:eval` (accepted). KB's issue
> profile justifies them — 11 open perf issues (#181, #199–#211, #213) and 6
> eval issues (#331, #332, #256, #261, #329, …) the user _already_ titles
> `perf:` / `eval:`. They keep `type:enhancement` and `type:research` from
> becoming dumping grounds, and `type:research`+`type:eval` together drive the
> Research & Eval board (§3.4).

### 1.2 `area:` — which subsystem (exactly one, required)

**Tiebreak rule:** `area:` = **the subsystem the code lands in** (a code
locator), _not_ the ROADMAP workstream. For eval/research issues the _activity_
is carried by `type:eval`/`type:research`, and `area:` still names the touched
subsystem. Example: #332 _"eval: benchmark EmbeddingGemma"_ → `area:embeddings`

- `type:eval` (not a fake `area:eval`).

| Label              | Subsystem (modules)                                                                                        | ROADMAP workstream       |
| ------------------ | ---------------------------------------------------------------------------------------------------------- | ------------------------ |
| `area:ingest`      | `ingest.py`, `chunking.py`, `web.py`, PDF path                                                             | Ingest                   |
| `area:search`      | `search.py`, `keywords.py`, `reranker.py`, `folder_summaries.py`, `prediction_errors.py`, `auto_relate.py` | Search                   |
| `area:embeddings`  | `embeddings.py`, `embed_swap.py`, quantization                                                             | Embedding                |
| `area:extraction`  | `extraction.py`, `llm.py`, entity resolution                                                               | Extraction               |
| `area:vision`      | `vision.py`, OmniParser, figures                                                                           | Vision                   |
| `area:papers`      | `papers.py`, `bibtex.py`, relationships, conclusions                                                       | Papers                   |
| `area:db`          | `db.py`, `_conn.py`, schema, migrations                                                                    | Foundation (split)       |
| `area:mcp`         | `server.py`, `routes/`, MCP tools, ACL                                                                     | Foundation (split)       |
| `area:infra`       | CI, build, jobs, indexer CLI, serve mode, scaling, packaging                                               | Foundation/Scale (split) |
| `area:integration` | memory-engine hooks, four-layer architecture                                                               | Integration              |
| `area:docs`        | documentation                                                                                              | Foundation (split)       |

> 11 areas. "Foundation" (a grab-bag) splits into `db`/`mcp`/`infra`/`docs`;
> "Scale" folds into `infra`. The migration uses the ROADMAP **Workstream**
> column as a _first-pass_ mapping, with the four Foundation→{db,mcp,infra,docs}
> reassignments done by hand (≈10 issues).

### 1.3 Cross-cutting (additive, optional)

- **`priority:critical|high|medium|low`** — scheduling urgency (see §2). Mirrors
  the Project **Priority** field 1:1. **No "deferred" tier** — deferral is
  expressed by **Phase = `Deferred`** (§3.1).
- **`severity:critical|high|medium|low|info`** — intrinsic technical impact of
  a **super-qa finding only**. Set by `/super-qa`, not hand-assigned. Distinct
  from priority (urgency): a `severity:high` super-qa finding may still be
  `priority:low` if it is not scheduled.
- **`status:blocked|needs-design`** — only when non-default. `blocked` =
  external dep (e.g. #262 on Ollama, #102 on memory-engine API);
  `needs-design` = design before code (#323).
- **`super-qa`** — provenance marker for `/super-qa` audit findings (kept).

> **Dropped `status:parked`** — redundant with **Phase = `Deferred`** (your
> refinement). The parking lot is now a phase value, not a status label.
> **No `tier:`** (reify-specific to its C++26 reflection roadmap; KB uses Phase
> instead). **No domain labels** like coraly's `rgpd`/`partnership` (KB is a
> single-tenant research tool). A `security` concern = `type:security`.

> **Word collision is intentional & safe:** `priority:high` and `severity:high`
> share the word `high` but differ by prefix and meaning (urgency vs impact).
> This is exactly reify's model. Legacy bare `high`/`medium`/`low` therefore map
> cleanly 1:1 — to `priority:*` on planning issues, `severity:*` on super-qa
> findings.

---

## 2. Priority (decided: critical / high / medium / low)

Exposed **both** as a `priority:` label _and_ a Project **Priority**
single-select field (same values, 1:1, no translation).

| Label               | Field option | Meaning                                    |
| ------------------- | ------------ | ------------------------------------------ |
| `priority:critical` | `Critical`   | Drop everything — blocks the project       |
| `priority:high`     | `High`       | On the critical path to the next milestone |
| `priority:medium`   | `Medium`     | Planned work                               |
| `priority:low`      | `Low`        | Nice to have                               |

**Deferral is orthogonal:** an issue that is "someday / no timeline" is not a
priority level — it is **Phase = `Deferred`** (the parking lot: #12, #65, #351,
#232, #233). A deferred issue carries no `priority:` label until it is pulled
back onto the roadmap. This keeps "how urgent" (priority) and "is it scheduled
at all" (phase) as independent axes.

The **label** drives greppability + automation triggers; the **field** drives
board sort/group. Keep them in sync (a triage discipline, enforced in
`CLAUDE.md`).

---

## 3. GitHub Projects (four boards)

### 3.1 `KB — Main` (every open issue)

The daily driver. Auto-populated. Custom fields:

| Field        | Type          | Options                                                                                             |
| ------------ | ------------- | --------------------------------------------------------------------------------------------------- |
| **Status**   | single-select | `Todo` · `In Progress` · `In Review` · `Done`                                                       |
| **Priority** | single-select | `Critical` · `High` · `Medium` · `Low`                                                              |
| **Phase**    | single-select | `2.5c` · `3A` · `3B` · `3C` · `3D` · `3E` · `3F` · `3G` · `3H` · `3I` · `4` · `4+` · **`Deferred`** |

> **Status:** `In Review` (the PR-open stage) added between In Progress and Done
> (accepted). Empty Phase = untriaged (new bugs land here until phased).

**Saved views:**

- **Board by Status** (kanban) — daily flow.
- **Table by Phase** (group: Phase, sort: Priority) — roadmap view; _this is
  the "epics in progress / upcoming" view_ (see §4). `Deferred` sorts last.
- **Table by Area** (group: `area:` label) — subsystem load.
- **Critical Path filter** (`priority:critical,high`) — what's hot.

### 3.2 `KB — Critical Path to Phase 4` (manually curated)

Mirrors the ROADMAP's "Critical Path to Phase 4" section and **your stated
priority (107 + 80 + 262)**. Members:

- **Gate items:** #325 (contextual retrieval), #326 (chunk enrichment),
  #328 (embedding versioning), #275 + #342 (ingest bugs).
- **Phase-4 targets:** #107 (code-indexing epic), #80 (web UI), #262
  (multimodal embedding).
- **Key unlock:** #253 (query-type classifier — unblocks 5 downstream).

Manually curated (matches reify). Same Status/Priority/Phase fields as Main.

### 3.3 `KB — Bug & Security Triage` (auto-populated by label)

`type:bug` + `type:security` + super-qa findings. Severity-ordered. Immediately
useful — KB has a large super-qa backlog (#181, #199–#233, #297, #298, #309,
#319). Own view sorted by **Severity** then **Priority**.

> **Sortable fields (added post-rollout):** a Projects table can only sort/group
> by a _field_, not a label — so this board carries its own `Priority`,
> `Severity`, and `Phase` single-select fields (`scripts/sync-board-fields.sh`),
> populated from the issues' `priority:`/`severity:` labels + Main's `Phase`, and
> re-synced on `migrate-issues.sh` re-runs. Projects fields are per-board, so
> these mirror — they do not share — Main's; the **labels stay the source of
> truth**.

### 3.4 `KB — Research & Eval` (auto-populated by label) — **accepted**

`type:research` + `type:eval`. KB carries an unusually large research/eval
workload that deserves its own surface (the coraly analog of its V1/Ederra
parallel-workstream boards):

- **Eval:** #332 (EmbeddingGemma), #331 (Gemma 4), #256 (olmOCR-bench),
  #261 (Bayesian pipeline opt), #329 (late chunking), #250 (golden set —
  prereq for eval), #247 (Kreuzberg).
- **Research / ADR:** #361 (Embedding Adapters V2), #362 (sem structural-hash),
  #268/#269 (Level-2 injection), the 3I ADR items.

Own view grouped by `type:` (Labels) then sorted by **Phase**. Carries its own
`Priority`/`Severity`/`Phase` fields (same mechanism as Triage §3.3 — see the
sortable-fields note there).

> **Why a board, not just a filter:** these items have a different cadence
> (deliverable = numbers or an ADR, not a merged feature) and often run in
> parallel with feature work. A dedicated board keeps them from cluttering the
> Main kanban while staying visible. Auto-populated, so zero upkeep.

---

## 4. Epics vs the Phase field (reconciling "epics in progress")

You asked for "epics, in progress and soon to be worked on." Two mechanisms,
deliberately split:

- **Phase field = roadmap grouping.** "What's in 3A / 3B / 3C / 3I?" is answered
  by the **Phase field** on Main (Table-by-Phase view). This is the
  "epics in progress and upcoming" picture, and it migrates **mechanically**
  from the ROADMAP table (~57 open issues already carry a phase). No sub-issue
  linking needed.

- **`type:epic` + native sub-issues = big decomposable features only.** Reserve
  epics for features that genuinely need child-issue tracking:
  - **#107** semantic code indexing (6 sub-phases — a real epic),
  - **#124** decouple index/serving (already an open umbrella: #349✔/#350/#351),
  - **#80** web UI (optional — large enough to decompose when started).

> Rationale: making an epic per subphase would force ~57 sub-issue links and
> duplicate the Phase field. The Phase field is free (set during migration) and
> gives the same grouping. Epics earn their keep only when a single feature
> explodes into many children. This is the reify model ("epics decompose into
> sub-issues") applied judiciously.

**Active vs upcoming epics/phases (from the ROADMAP):**

| Phase    | Theme                     | State                                | Gate?             |
| -------- | ------------------------- | ------------------------------------ | ----------------- |
| 2.5c     | super-qa medium findings  | in progress (perf/quality/docs open) | tech-debt         |
| 3A       | search quality            | **next / in progress**               | **critical path** |
| 3B       | embedding & quantization  | upcoming                             | important         |
| 3C       | ingest pipeline (+bugs)   | upcoming (bugs first)                | mixed             |
| 3I       | April-2026 landscape gaps | **in progress (ADRs drafted)**       | mixed             |
| 3E       | memory-engine integration | upcoming                             | external dep      |
| 3F       | multi-agent safety        | upcoming                             | optional          |
| 4        | #107 / #80 / #262         | target                               | epic-worthy       |
| Deferred | parking lot               | #12 / #65 / #351 / #232 / #233       | —                 |

---

## 5. Automation (`.github/workflows/`)

### 5.1 `add-to-project.yml` (replicate reify, adapted — now 3 jobs)

```yaml
name: Auto-add to project
on:
  issues:
    types: [opened, reopened, labeled]
  pull_request:
    types: [opened, labeled]
jobs:
  add-to-main:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/add-to-project@v1
        with:
          project-url: https://github.com/users/dutiona/projects/<MAIN_N>
          github-token: ${{ secrets.KB_PROJECT_TOKEN }}
  add-to-triage:
    runs-on: ubuntu-latest
    if: >-
      (github.event_name == 'issues' &&
        (contains(github.event.issue.labels.*.name, 'type:bug') ||
         contains(github.event.issue.labels.*.name, 'type:security'))) ||
      (github.event_name == 'pull_request' &&
        (contains(github.event.pull_request.labels.*.name, 'type:bug') ||
         contains(github.event.pull_request.labels.*.name, 'type:security')))
    steps:
      - uses: actions/add-to-project@v1
        with:
          project-url: https://github.com/users/dutiona/projects/<TRIAGE_N>
          github-token: ${{ secrets.KB_PROJECT_TOKEN }}
  add-to-research-eval:
    runs-on: ubuntu-latest
    if: >-
      (github.event_name == 'issues' &&
        (contains(github.event.issue.labels.*.name, 'type:research') ||
         contains(github.event.issue.labels.*.name, 'type:eval'))) ||
      (github.event_name == 'pull_request' &&
        (contains(github.event.pull_request.labels.*.name, 'type:research') ||
         contains(github.event.pull_request.labels.*.name, 'type:eval')))
    steps:
      - uses: actions/add-to-project@v1
        with:
          project-url: https://github.com/users/dutiona/projects/<RESEARCH_N>
          github-token: ${{ secrets.KB_PROJECT_TOKEN }}
```

> **Event-aware conditions (per PR #365 review):** `github.event.issue` is null
> on `pull_request` events, so the triage/research jobs branch on
> `github.event_name` and read `github.event.pull_request.labels` for PRs. The
> `labeled` trigger ensures items route when a `type:` label is applied _after_
> creation (the common triage flow), not only at open. `add-to-project` is
> idempotent, so the extra `labeled`/`reopened` runs are safe no-ops.

> **Out-of-band setup (manual, like coraly's `deploy:prod`):** the Action needs
> a **classic PAT** with **`repo` + `project`** scope, stored as repo secret
> **`KB_PROJECT_TOKEN`**. The default `GITHUB_TOKEN` _cannot_ write to
> user-level Projects v2 — without this secret the workflow 403s on the first
> issue. (Your interactive `gh` token already has `project` scope, which is why
> `gh project list` worked — but Actions uses a different token.)

Critical Path board is **manual** (no auto-job), matching reify.

### 5.2 Labels-as-code (`scripts/sync-labels.sh`)

An idempotent `gh label create … --force` script defining the full taxonomy
(names, colors, descriptions). Run once to migrate, re-run to converge. Colors
borrow the reify palette per dimension (type=varied, area=blue/green family,
`priority:critical`/`severity:critical`=`b60205`, etc.). This is the
reproducible migration mechanism + living source of truth.

### 5.3 CI (`ci.yml`) — **accepted**

KB has **no CI at all**; both reference repos do. Minimal gate on push/PR:

```yaml
name: CI
on: [push, pull_request]
jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - run: uv run ruff check src/ tests/
      - run: uv run ruff format --check src/ tests/
      - run: uv run pytest -q -m "not slow"
```

> `-m "not slow"` excludes the `@pytest.mark.slow` tests that need a live
> Ollama/network (per CLAUDE.md testing conventions). A follow-up can add a
> nightly job that runs the slow suite against a service container if wanted.

---

## 6. Migration (open issues only; staged)

The ROADMAP table is the migration **oracle**, but it is _not_ fully mechanical
— be honest about that.

**Mechanical (scriptable):**

- Workstream → `area:` first pass (with Foundation→{db,mcp,infra,docs} by hand).
- ROADMAP Phase column → Project **Phase** field (incl. parking-lot → `Deferred`).
- Title prefix → `type:` (feat→feature, fix→bug, perf→perf, eval→eval, …).

**Semi-manual (per-issue judgment — the script _cannot_ infer these):**

- Legacy bare `high`/`medium`/`low`/`info`: **context-dependent** —
  `severity:*` on super-qa issues, `priority:*` elsewhere (1:1 word match now).
- `enhancement`: split `type:feature` (new) vs `type:enhancement` (improve).
- Priority assignment for issues the roadmap doesn't rank.

**Legacy label disposition:**

| Legacy label                                                                | New mapping                                              |
| --------------------------------------------------------------------------- | -------------------------------------------------------- |
| `bug`                                                                       | `type:bug`                                               |
| `enhancement`                                                               | `type:feature` _or_ `type:enhancement` (judge)           |
| `documentation`                                                             | `type:docs` (+ `area:docs`)                              |
| `refactoring`                                                               | `type:refactor`                                          |
| `research`                                                                  | `type:research`                                          |
| `security`                                                                  | `type:security`                                          |
| `database`                                                                  | `area:db`                                                |
| `retrieval`                                                                 | `area:search`                                            |
| `quality`                                                                   | `type:enhancement` + relevant `area:` (judge)            |
| `plan`                                                                      | `type:plan`                                              |
| `high`/`medium`/`low`                                                       | `severity:*` (super-qa) **or** `priority:*` (judge, 1:1) |
| `info`                                                                      | `severity:info`                                          |
| `super-qa`                                                                  | keep                                                     |
| `duplicate`/`invalid`/`wontfix`/`help wanted`/`good first issue`/`question` | delete (unused noise on a single-dev research repo)      |

**Staging (don't let migration block the system):**

1. Stand up the _system_ first: labels, 4 Projects + fields, automation, CI, docs.
2. Then migrate: generate a **mapping table** (one row per open issue:
   `#`, current labels → `type:`/`area:`/`priority:`/Phase) → **you approve** →
   script applies. New issues from day 1 follow the convention.

---

## 7. Documentation

- Add a **`## Project Management`** section to KB `CLAUDE.md` (taxonomy, the 4
  boards, the workflow, triage rules, the sub-issue GraphQL snippet) — this is
  what makes the convention stick across Claude/Codex/Gemini sessions.
- **Unify** `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` (currently 83/63/62 lines —
  _not_ identical, unlike reify/coraly). Make CLAUDE.md canonical, copy to the
  other two, document the equivalence rule.
- Update `ROADMAP.md` header to point at the Projects as the live tracker
  (keep ROADMAP as the dependency-graph narrative; Projects = live state).

---

## 8. Remaining open decision points (minor — defaults chosen)

1. **`Blocked` status column?** Default **no** — `status:blocked` label + a
   filtered view suffices. (Add only if you want it on the kanban.)
2. **Default-label cleanup** — default **delete** the unused GitHub defaults
   (`duplicate`/`invalid`/`wontfix`/`help wanted`/`good first issue`/`question`).
   Say if you'd rather keep any.
3. **Milestones** — default **none** (both references rely on Phase field, not
   milestones). Phase field is the milestone analog.

Everything else from round 1 is locked (see "Decisions locked" at top).

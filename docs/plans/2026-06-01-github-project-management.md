# Plan: GitHub Projects PM System for knowledge-base

> **Date:** 2026-06-01 · **Slug:** `github-project-management`
> **Design (validated):** [docs/design/project-management.md](../design/project-management.md)
> **Execution gate (user):** Plan + execute everything, **staged**, pausing only
> at the migration mapping-table checkpoint.
> **Division of labor (user):** _I_ create projects + fields + labels + workflow
> files + migration; _user_ creates all views/boards/tables (from filter specs
> herein), adds the "In Review" Status option, sets the PAT secret, and activates
> the auto-add workflows.
> **Reviews:** clean-slate subagent + Codex + Gemini (advisor unavailable — see
> [dutiona/my-dotfiles#82](https://github.com/dutiona/my-dotfiles/issues/82);
> gemini fixed via `serverUrl`→`httpUrl` in `~/.gemini/settings.json`).

---

## 0. Objective & guardrails

Stand up a disciplined `prefix:value` label taxonomy, four GitHub Projects with
custom fields, issue/PR automation, CI, and migrate **open issues only** onto the
scheme — for `dutiona/knowledge-base`. Adapted from `dutiona/reify` and
`Corely-Cycle/coraly-cycle`.

**Guardrails (every phase):**

- **Idempotent + `--dry-run`** on every script (print intended mutations, change
  nothing; safe to re-run, converges).
- **Rename, don't delete, where associations matter** (migration backbone).
- **Open issues only.** Closed issues never relabeled. (Label _renames_
  unavoidably touch closed issues too — desirable: fixes their labels for free.)
- **Repo-state vs files.** File artifacts (`scripts/`, `.github/workflows/`,
  docs) ship via **worktree → PR → squash-merge**. GitHub-state mutations
  (labels, projects, issue fields) run via `gh` and are not VCS-tracked — but the
  _scripts_ are.
- **Hard checkpoint.** The ~95-issue migration pauses for explicit approval of
  the generated mapping table before any issue is touched.

---

## 0.5 Division of labor (authoritative)

| Step                                  |  Owner  | Mechanism                                   |
| ------------------------------------- | :-----: | ------------------------------------------- |
| Labels (taxonomy, rename, delete)     | **me**  | `scripts/sync-labels.sh`                    |
| Project _containers_ (4)              | **me**  | `gh project create`                         |
| Custom fields **Priority**, **Phase** | **me**  | `gh project field-create`                   |
| "In Review" option on built-in Status | **you** | Project web UI (no `gh field-edit`)         |
| All **views / boards / tables**       | **you** | Project web UI, from §5 view-spec sheet     |
| Workflow + CI **files**               | **me**  | committed YAML in the PR                    |
| `KB_PROJECT_TOKEN` PAT secret         | **you** | `gh secret set` / repo settings             |
| **Activate** auto-add workflows       | **you** | merge PR + secret present                   |
| Issue migration (labels+fields+add)   | **me**  | `scripts/migrate-issues.sh` (post-approval) |
| Critical-Path board membership        | **me**  | `gh project item-add` (fixed list)          |
| Docs (CLAUDE/AGENTS/GEMINI, ROADMAP)  | **me**  | committed in the PR                         |

> **Why this split:** `gh` exposes `project create` / `field-create` / `item-*`
> but **no `view-create`** (views are UI-only) and **no `field-edit`** (can't add
> an option to the built-in Status field). So view/board creation and the
> "In Review" option are necessarily yours; everything scriptable is mine.

---

## 1. Critical decisions (locked) & lens rationale

| Axis            | Decision                                                                                                                                |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `type:`         | bug feature enhancement perf refactor test docs chore research eval security epic plan (13)                                             |
| `area:`         | ingest search embeddings extraction vision papers db mcp infra integration docs (11)                                                    |
| `priority:`     | critical high medium low (4) — label **and** Project field, 1:1                                                                         |
| `severity:`     | critical high medium low info (5) — super-qa findings only                                                                              |
| `status:`       | blocked needs-design (2) — `parked` dropped (Phase=Deferred replaces it)                                                                |
| `super-qa`      | kept                                                                                                                                    |
| Delete defaults | duplicate, invalid, wontfix, help wanted, good first issue, question                                                                    |
| Projects        | KB—Main · KB—Critical Path to Phase 4 · KB—Bug & Security Triage · KB—Research & Eval                                                   |
| Main fields     | Status[Todo,In Progress,**In Review**,Done] · Priority[Critical,High,Medium,Low] · Phase[2.5c,3A,3B,3C,3D,3E,3F,3G,3H,3I,4,4+,Deferred] |
| No              | Blocked status column · milestones                                                                                                      |

**Lens application (mvp / risk / architecture):**

- **mvp-first → ordering.** Thinnest working slice = labels + KB—Main container +
  Priority/Phase fields. Everything else layers on without blocking it.
  Destructive/irreversible steps come last.
- **risk-first → the dangerous four**, each mitigated in-phase:
  1. _Label deletion_ removes the label everywhere (incl. closed). → Phase 2
     usage-report-then-delete; only the 6 noise defaults.
  2. _~95-issue relabel_ with a wrong mapping. → Phase 6 approval checkpoint +
     dry-run + `⚠` flags.
  3. _`field-create` not idempotent_ (dupes on re-run). → Phase 3 `field-list`
     check before create.
  4. _Missing PAT_ → automation 403s silently. → workflow inert until you set
     the secret; go-live verified by a test issue.
- **architecture-first → script boundaries.** Independently-runnable, each
  idempotent + `--dry-run`: `sync-labels.sh`, `setup-projects.sh` (prints IDs to
  `.pm-ids.env`), `gen-mapping.sh` (read-only → `mapping.tsv`),
  `migrate-issues.sh` (consumes approved TSV). They communicate via GitHub state
  - files, never in-memory coupling.

**Verified `gh` facts** (confirmed this session, `gh` 2.45.0):
`gh label edit <old> --name <new>` renames in place preserving associations ✓;
`gh label create --force` upserts ✓; `gh project field-create` exists with
`--single-select-options` ✓ but **no `field-edit`/`view-create`** ✗.

---

## 2. Phase 1 — Worktree & scaffold

1. `git worktree add .worktrees/feat-github-pm -b feat/github-project-management`
   (off `master`). All file edits happen here.
2. Create `scripts/`, `.github/workflows/`.
3. `scripts/lib/pm-common.sh` — `OWNER=dutiona`, `REPO=knowledge-base`,
   `dry_run` guard, `log`, `gh` presence check, `confirm()` for destructive steps.

**Exit:** worktree + structured skeleton committed.

---

## 3. Phase 2 — `scripts/sync-labels.sh` (labels; **mine**, idempotent)

1. **Rename-in-place FIRST** unambiguous legacy labels (preserves associations,
   guarded by exists-check). **Order matters** (per PR #365 review): rename
   _before_ the upsert, else `gh label edit bug --name type:bug` collides with a
   `type:bug` the upsert would have just created, and fails.
   `database→area:db`, `retrieval→area:search`, `refactoring→type:refactor`,
   `research→type:research`, `security→type:security`, `plan→type:plan`,
   `bug→type:bug`, `documentation→type:docs`.
2. **Then upsert** the full taxonomy: `gh label create <name> --color <hex>
--description <desc> --force`. Idempotent, so any label already produced by
   step 1's rename is a safe no-op. Colors per design palette
   (`priority:critical`/`severity:critical`=`b60205`, area=blue/green, etc.).
3. **Leave ambiguous legacy labels** for Phase 6 per-issue resolution:
   `enhancement`, `quality`, `high`, `medium`, `low`, `info`.
4. **Delete noise defaults** — usage-report first (`gh issue list --label <l>
--state all -L 1`), print counts, then `gh label delete <l> --yes` for:
   duplicate, invalid, wontfix, help wanted, good first issue, question.
5. `--dry-run` echoes every mutation.

**Exit:** full taxonomy present; unambiguous legacy renamed; noise gone; ambiguous
labels still present (intentional).

---

## 4. Phase 3 — `scripts/setup-projects.sh` (containers + fields; **mine**)

Check-before-create throughout (projects/fields are not idempotent).

1. For each of the 4 titles: look up in `gh project list --owner dutiona
--format json`; create only if absent. Capture each project **number**.
2. On **KB—Main**, ensure custom fields (query `field-list` first):
   - `Priority` SINGLE_SELECT → `Critical,High,Medium,Low`
   - `Phase` SINGLE_SELECT → `2.5c,3A,3B,3C,3D,3E,3F,3G,3H,3I,4,4+,Deferred`
   - **Status `In Review`** → **NOT scriptable** (no `field-edit`). Emit a
     reminder line: _"USER: add 'In Review' to the Status field in the Main board
     UI (Settings → Status → + Add option, between In Progress and Done)."_
3. Apply Priority/Phase to **KB—Critical Path** too (same schema).
4. Write `scripts/.pm-ids.env` (gitignored): project numbers, URLs, field IDs,
   option IDs — consumed by `migrate-issues.sh` and to fill the workflow YAML
   project numbers.
5. `--dry-run` supported.

**Exit:** 4 containers exist; Main + Critical-Path carry Priority/Phase;
`.pm-ids.env` written; the In-Review reminder printed for you.

---

## 5. Phase 4 — View-spec sheet (**handoff to you**)

`gh` can't make views. This sheet is what you build in each Project's UI. Filter
syntax is GitHub Projects' (`label:"type:bug"`, `is:open`, field filters like
`priority:High`, `phase:3A`).

### KB — Main (auto-add: all)

| View name             | Layout | Filter                                              | Group by | Sort       |
| --------------------- | ------ | --------------------------------------------------- | -------- | ---------- |
| **Board**             | Board  | `is:open`                                           | Status   | —          |
| **Roadmap by Phase**  | Table  | `is:open`                                           | Phase    | Priority ↓ |
| **By Area**           | Table  | `is:open`                                           | Labels¹  | Priority ↓ |
| **Hot (P-crit/high)** | Table  | `is:open label:"priority:critical","priority:high"` | Priority | Phase ↑    |

¹ _Caveat:_ Projects "group by Labels" creates a lane per label (type/area/etc.),
not area-only. For an area-only view, either filter one area at a time
(`label:"area:search"`) or use the **Slice by → Labels** side panel. Documented
honestly — there's no clean single-field area grouping without an extra `Area`
custom field (deferred; the `area:` label is the source of truth).

### KB — Critical Path to Phase 4 (manual membership; I add the items)

| View name         | Layout | Filter          | Group by | Sort       |
| ----------------- | ------ | --------------- | -------- | ---------- |
| **Critical Path** | Board  | (none — manual) | Status   | Priority ↓ |

Members I add: #325, #326, #328, #275, #342, #107, #80, #262, #253.

### KB — Bug & Security Triage (auto-add: `type:bug`||`type:security`)

| View name  | Layout | Filter                                     | Group by | Sort                 |
| ---------- | ------ | ------------------------------------------ | -------- | -------------------- |
| **Triage** | Table  | `is:open label:"type:bug","type:security"` | —        | Labels (severity)² ↓ |

² Sort by the `severity:` label ordering (critical→info), then Priority.

### KB — Research & Eval (auto-add: `type:research`||`type:eval`)

| View name           | Layout | Filter                                      | Group by      | Sort    |
| ------------------- | ------ | ------------------------------------------- | ------------- | ------- |
| **Research & Eval** | Table  | `is:open label:"type:research","type:eval"` | Labels (type) | Phase ↑ |

---

## 6. Phase 5 — Automation & CI files (**mine**, committed)

1. `.github/workflows/add-to-project.yml` — 3 jobs (project numbers from
   `.pm-ids.env`). Triggers `issues:[opened,reopened,labeled]` +
   `pull_request:[opened,labeled]` (the `labeled` trigger routes items when a
   `type:` label is applied post-creation — the normal triage flow):
   - `add-to-main` — always.
   - `add-to-triage` — event-aware `if`: `type:bug`/`type:security`, reading
     `github.event.issue.labels` for `issues` and
     `github.event.pull_request.labels` for `pull_request` (the `issue` object is
     null on PR events — see design §5.1, per PR #365 review).
   - `add-to-research-eval` — same event-aware `if` for `type:research`/`type:eval`.
   - `actions/add-to-project@v1`, token `${{ secrets.KB_PROJECT_TOKEN }}`.
2. `.github/workflows/ci.yml` — `[push, pull_request]`: `astral-sh/setup-uv@v5` →
   `uv sync` → `uv run ruff check src/ tests/` → `uv run ruff format --check
src/ tests/` → `uv run pytest -q -m "not slow"`.
3. **You (out-of-band):** classic PAT, `repo`+`project` scope →
   `gh secret set KB_PROJECT_TOKEN`. Workflows are inert until merged + secret
   present (harmless before).

**Exit:** both YAML committed; PAT/activation is your step, verified post-merge.

---

## 7. Phase 6 — Documentation (**mine**, committed)

1. `## Project Management` section in `CLAUDE.md` (canonical): taxonomy, title
   convention `type(area): description`, the 4 boards + what auto-populates each,
   "exactly one `type:` + one `area:`" rule, Phase=Deferred for parking, the
   `addSubIssue` GraphQL snippet for `type:epic` children, and the
   division-of-labor note.
2. **Unify** → copy `CLAUDE.md` verbatim to `AGENTS.md` + `GEMINI.md` + a
   file-equivalence line. _(Open Q for review: full unification vs shared-PM-
   section-only, given KB's AGENTS/GEMINI are currently trimmed.)_
3. `ROADMAP.md` header: "GitHub Projects is the live tracker; ROADMAP is the
   dependency-graph narrative." Map ROADMAP Phase ↔ Project Phase field.
4. `scripts/README.md`: how to run each script, `--dry-run`, PAT setup,
   `.pm-ids.env` contract, the view-spec sheet pointer.

**Exit:** docs in worktree; three config files byte-identical (`diff` clean).

---

## 8. Phase 7 — Staged issue migration (the checkpoint; **mine** + your approval)

### 8a — `scripts/gen-mapping.sh` (read-only) → `scripts/mapping.tsv`

One row per **open** issue (~95): `#  title  current_labels  →  type:  area:
priority:  phase  needs_review`.

- **type:** title prefix (`feat:`→feature, `fix:`→bug, `perf:`→perf,
  `eval:`→eval, `research:`/`design:`→research, `refactor:`→refactor, `docs:`,
  `chore:`, `epic:`) + legacy `bug`/`enhancement`.
- **area:** ROADMAP Workstream first-pass, Foundation→{db,mcp,infra,docs}
  reassignments flagged `⚠`.
- **priority/severity:** super-qa issues → `severity:*` from bare high/med/low/info;
  non-super-qa bare high/med/low → `priority:*`; else blank.
- **phase:** ROADMAP Phase → Project Phase (parking → `Deferred`).
- `⚠` marks any row needing human judgment.

### 8b — **HARD CHECKPOINT: you approve `mapping.tsv`** (only pause)

### 8c — `scripts/migrate-issues.sh mapping.tsv`

Per row, idempotently:

1. `gh issue edit <#> --add-label … --remove-label <legacy>`.
2. **Add to Main and capture the project item-id** (per PR #365 review —
   `item-edit` targets the project-internal **item-id**, NOT the issue number or
   URL):
   ```bash
   ITEM_ID=$(gh project item-add <MAIN_N> --owner dutiona \
       --url "$ISSUE_URL" --format json --jq '.id')
   ```
   (Re-runnable: `item-add` on an already-added issue returns the existing item,
   so the captured id is stable.)
3. Set fields with that id + the field/option IDs from `.pm-ids.env`:
   ```bash
   gh project item-edit --id "$ITEM_ID" --project-id "$MAIN_PID" \
       --field-id "$PHASE_FIELD_ID"    --single-select-option-id "$PHASE_OPT_ID"
   gh project item-edit --id "$ITEM_ID" --project-id "$MAIN_PID" \
       --field-id "$PRIORITY_FIELD_ID" --single-select-option-id "$PRIO_OPT_ID"
   ```

Then add the fixed Critical-Path member list (same item-add → capture-id
pattern). `--dry-run` prints every call; re-runnable.

> `gen-mapping.sh`/`setup-projects.sh` must therefore record, in `.pm-ids.env`,
> the **project node-id** (`$MAIN_PID`), each **field-id**, and the
> **option-id per single-select value** — `item-edit` needs all three, not the
> human-readable names.

### 8d — Final cleanup

Delete now-unreferenced ambiguous legacy labels (`enhancement`, `quality`,
`high`, `medium`, `low`, `info`) with usage-report-then-delete safety.

**Exit:** every open issue has `type:`+`area:`(+`priority:`), on KB—Main with
Phase+Priority; bugs/security auto-flow to Triage; research/eval to Research&Eval;
Critical-Path populated; legacy labels gone.

---

## 9. Phase 8 — PR, review, merge (operational)

1. **Plan issue:** publish this plan verbatim as a GitHub issue, labeled
   `type:plan` + `area:infra` (dogfoods the taxonomy). Reference in the PR.
2. **PR:** commit worktree (scripts, workflows, docs) → push → PR vs `master`,
   linking the plan issue.
3. **Review:** `/super-review` (Codex+Gemini) on the PR.
4. **You:** set `KB_PROJECT_TOKEN`; after merge, open a throwaway issue to confirm
   auto-add fires; close it.
5. **Merge:** squash-merge into `master` (rebase if needed); remove worktree.

---

## 10. Documentation (super-plan required section)

Covered by Phase 5 + the design doc. New user-facing surface = scripts + the
label/triage convention + the view-spec sheet; examples in `scripts/README.md`
and the CLAUDE.md PM section. **Not N/A.**

## 11. Testing (super-plan required section)

- **Static:** `shellcheck scripts/*.sh scripts/lib/*.sh`; `actionlint` (or
  `yamllint`) on both workflows.
- **Dry-run as test:** every script `--dry-run` first; `migrate-issues.sh` output
  diffed against approved `mapping.tsv`.
- **Idempotency:** run `sync-labels.sh` + `setup-projects.sh` twice; second run
  = zero new mutations.
- _N/A: no pytest-level suite for the bash glue_ — these are one-shot `gh`
  orchestration scripts; correctness = dry-run + idempotency + live
  `gh label/project list` end-state. The repo's `pytest` suite is unaffected (CI
  only _adds_ a gate; no `src/` change).

## 12. Verification (super-plan required section)

```bash
shellcheck scripts/*.sh scripts/lib/*.sh
actionlint .github/workflows/*.yml
gh label list -R dutiona/knowledge-base                 # full taxonomy, noise gone
gh project field-list <MAIN_N> --owner dutiona          # Priority + Phase present
gh project list --owner dutiona | grep -c 'KB —'        # 4 containers
bash scripts/sync-labels.sh --dry-run                   # idempotent: nothing pending
diff CLAUDE.md AGENTS.md && diff CLAUDE.md GEMINI.md     # byte-identical
gh secret list -R dutiona/knowledge-base | grep KB_PROJECT_TOKEN   # PAT present (your step)
uv run ruff check src/ tests/ && uv run pytest -q -m "not slow"    # repo green
```

Spot-check open issues: exactly one `type:`, one `area:`, on KB—Main with Phase.
**Your manual verification:** the 4 boards' views match §5; "In Review" present on
Status; a test issue auto-adds after merge.

---

## 13. Risks & mitigations

| Risk                                         | Mitigation                                              |
| -------------------------------------------- | ------------------------------------------------------- |
| Label delete loses associations (closed too) | Phase 2 usage-report-then-delete; only 6 noise defaults |
| Wrong per-issue mapping (~95)                | Phase 8b checkpoint + dry-run + `⚠` flags               |
| `field-create` duplicates on re-run          | Phase 3 `field-list` check first                        |
| Status `In Review` not addable via gh        | Your one-time UI step (§0.5, Phase 3 reminder)          |
| No `view-create` in gh                       | Views are your UI step from §5 specs                    |
| Missing PAT → silent 403                     | Inert until you set secret; verified by test issue      |
| Project mutations not VCS-tracked            | Scripts committed + idempotent → reproducible           |

---

## 14. Execution order (post-approval)

```
approve plan
  → Phase 1 worktree+skeleton
  → Phase 2 sync-labels.sh        (--dry-run → review → apply)         [me]
  → Phase 3 setup-projects.sh     (--dry-run → review → apply, .pm-ids.env)  [me]
  → Phase 4 view-spec sheet handed to you; YOU build views + In Review  [you]
  → Phase 5 workflows + CI committed; YOU set PAT secret               [me files / you secret]
  → Phase 6 docs (CLAUDE/AGENTS/GEMINI unify, ROADMAP, scripts/README)  [me]
  → Phase 7a gen-mapping.sh → mapping.tsv                              [me]
  → ⛔ Phase 7b YOU APPROVE mapping.tsv         ← only pause
  → Phase 7c migrate-issues.sh (--dry-run → apply) + Critical-Path     [me]
  → Phase 7d delete spent ambiguous labels                            [me]
  → Phase 8 plan issue → PR → /super-review → YOU activate workflows → squash-merge  [both]
```

Pauses: **Phase 7b** (mapping approval) + your parallel UI work (views, In Review,
PAT). Every mutating script is gated behind its own `--dry-run`.

# PM scripts

Idempotent `gh`-CLI orchestration for the knowledge-base project-management
system. Design: [../../docs/design/project-management.md](../../docs/design/project-management.md).

Every mutating script supports **`--dry-run`** (print intended `gh` calls,
change nothing) and is safe to re-run.

## Order

```bash
utils/scripts/sync-labels.sh    --dry-run   # then without --dry-run to apply
utils/scripts/setup-projects.sh --dry-run   # then apply → writes utils/scripts/.pm-ids.env
# (USER: build views, add "In Review" to Status, set KB_PROJECT_TOKEN — see below)
utils/scripts/gen-mapping.sh                 # read-only → utils/scripts/mapping.tsv
#   ⛔ review/approve mapping.tsv (resolve every ⚠ row) before the next step
utils/scripts/migrate-issues.sh --dry-run    # then apply
```

## Scripts

| Script              | Mutating? | What                                                                  |
| ------------------- | :-------: | --------------------------------------------------------------------- |
| `lib/pm-common.sh`  |     —     | shared helpers (`run` dry-run guard, `confirm`, `preflight`, logging) |
| `sync-labels.sh`    |    yes    | rename legacy → upsert taxonomy → delete noise (report usage first)   |
| `setup-projects.sh` |    yes    | create 4 Projects + Priority/Phase fields; write `.pm-ids.env`        |
| `gen-mapping.sh`    |  **no**   | propose per-issue labels/phase from ROADMAP + gh → `mapping.tsv`      |
| `migrate-issues.sh` |    yes    | apply approved `mapping.tsv`; populate Critical-Path board            |

## `.pm-ids.env` (generated, gitignored)

`setup-projects.sh` writes project numbers, the Main project node-id, the
Phase/Priority field-ids, and a `*_OPT_*` var per single-select option.
`migrate-issues.sh` sources it — `gh project item-edit` targets these **ids**,
not human-readable names.

## USER steps (`gh` cannot script these)

1. **In Review status** — KB — Main → Settings → Status → add option _In Review_
   (between _In Progress_ and _Done_). `gh` has no `field-edit`.
2. **Views/boards** — build per the saved-views spec in `../../docs/design/project-management.md`. `gh` has no
   `view-create`.
3. **PAT** — classic token, scopes `repo` + `project`:
   `gh secret set KB_PROJECT_TOKEN -R dutiona/knowledge-base`. The workflow
   `add-to-project.yml` is inert until this exists.
4. **Fill workflow project numbers** — replace `MAIN_NUMBER` / `TRIAGE_NUMBER` /
   `RESEARCH_NUMBER` in `.github/workflows/add-to-project.yml` from `.pm-ids.env`.

# Gemini review — GitHub PM plan

Two channels: **(A)** Gemini Code Assist inline review on PR #365; **(B)** a
one-shot `gemini` CLI review of the plan + design. Both ran after the gemini
config fix (`serverUrl`→`httpUrl` in `~/.gemini/settings.json`).

## Channel A — Gemini Code Assist (inline, PR #365), 3 findings

- **[MEDIUM] `add-to-project.yml` missing `labeled` trigger** (design §5.1:293).
  Items labeled _after_ creation (the normal triage flow) would never route.
- **[MEDIUM] `add-to-triage` reads null on PR events** (design §5.1:306).
  `github.event.issue` is null for `pull_request`; must read
  `github.event.pull_request.labels`, branching on `github.event_name`.
- **[MEDIUM] `add-to-research-eval` same null-on-PR bug** (design §5.1:316).

## Channel B — Gemini one-shot CLI, 3 findings

- **[HIGH] Label migration ordering (Phase 2).** Upserting the taxonomy _before_
  renaming legacy labels makes `gh label edit bug --name type:bug` collide with
  the just-created `type:bug`. Rename FIRST, then upsert.
- **[HIGH] Project item-id capture (Phase 7).** `gh project item-edit` targets
  the project **item-id**, not the issue number/URL. The migration script must
  capture `.id` from `gh project item-add --format json`.
- **[LOW] Full doc unification** of CLAUDE/AGENTS/GEMINI (already the plan's
  Phase 6 intent).

Technical claims Gemini independently **verified**: `gh label edit` renames +
preserves associations; `gh label create --force` upserts; `field-create`
non-idempotent; `add-to-project` needs a PAT; no `gh` `view-create`/`field-edit`.

## Resolution

- **[MEDIUM ×3 inline]** → Fixed in commit `81cb970`: added `labeled` to both
  triggers; both label-gated jobs now branch on `github.event_name` and read
  `github.event.pull_request.labels` for PRs. All 3 PR #365 review threads
  replied-to and **resolved** (GraphQL: 0 unresolved).
- **[HIGH] label ordering** → Fixed: Phase 2 reordered to rename-before-upsert
  with an explicit collision-rationale note.
- **[HIGH] item-id capture** → Fixed: Phase 7c now captures `ITEM_ID` from
  `item-add --format json` and passes it to `item-edit --id`, with the field/
  option-id contract spelled out for `.pm-ids.env`.
- **[LOW] doc unification** → No change needed; already specified (Phase 6 §2).
- **Codex** channel produced no findings (stdin stall); recorded, not fabricated.

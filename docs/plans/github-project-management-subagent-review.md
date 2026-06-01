# Clean-slate subagent review — GitHub PM plan

> Reviewer: `general-purpose` subagent, no conversation context, full repo + `gh`
> read access. Plan reviewed: `docs/plans/2026-06-01-github-project-management.md`.

## Findings

- **[BLOCKER] `gh` cannot add "In Review" to the built-in Status field.** There
  is no `gh project field-edit`, and `field-create` only creates _new_ fields —
  it cannot add an option to an existing single-select (including the built-in
  Status). The plan's "add In Review to Status" step is not achievable via `gh`.
  Verified: `gh project --help` exposes only `field-create`/`field-delete`/
  `field-list` (no `field-edit`).
  - _Deeper consequence (teased):_ the same limitation means any future
    field-option change requires delete+recreate (which detaches existing items).

- **[Confirmed feasible]** `gh label edit <old> --name <new>` renames in place
  preserving associations; `gh label create --force` upserts.

- **[Confirmed]** `gh project field-create` is non-idempotent → the plan's
  check-before-create (`field-list` first) is the right guard.

(The subagent's review was cut short when it attempted an `advisor()` call —
which is non-functional this session, see dutiona/my-dotfiles#82 — consuming its
final turn. The findings above are what it produced before that point; the
Status BLOCKER is the substantive one and is fully actionable.)

## Resolution

- **[BLOCKER] In Review via gh** → **Resolved by design, not workaround.** Folded
  into the user's half of the division of labor (§0.5): the user adds the
  "In Review" Status option in the Project web UI (a one-time ~30s step), and
  builds all views there too (since `gh` also has no `view-create`). Phase 3 now
  emits an explicit reminder line for this. The blocker is dissolved — it was
  never agent-scriptable, and the work it represents is now correctly assigned.
- No other findings required plan changes (feasibility confirmations).

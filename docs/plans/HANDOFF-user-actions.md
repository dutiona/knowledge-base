# PM rollout — your manual steps (the `gh`-can't-script half)

The agent has created the labels, the 4 Projects + fields, the workflow files,
and migrated the open issues. These remaining steps are **web-UI-only** (`gh` has
no `view-create` / `field-edit`) or require **account-level secrets**.

## 1. Add the "In Review" Status option (KB — Main, #7)

Project [KB — Main](https://github.com/users/dutiona/projects/7) →
**⋯ → Settings → Status field → + Add option** → name it **`In Review`**, drag it
between **In Progress** and **Done**. (Repeat on
[KB — Critical Path #8](https://github.com/users/dutiona/projects/8) if you want
the same column there.)

## 2. Build the saved views

Per the view-spec sheet in
[docs/plans/2026-06-01-github-project-management.md](2026-06-01-github-project-management.md)
§5. Quick version:

**KB — Main (#7):**

| View             | Layout | Filter                                              | Group / Sort                   |
| ---------------- | ------ | --------------------------------------------------- | ------------------------------ |
| Board            | Board  | `is:open`                                           | Group: Status                  |
| Roadmap by Phase | Table  | `is:open`                                           | Group: Phase; Sort: Priority ↓ |
| By Area          | Table  | `is:open`                                           | Slice: Labels (area)           |
| Hot              | Table  | `is:open label:"priority:critical","priority:high"` | Sort: Phase ↑                  |

**KB — Bug & Security Triage (#9):** Table, `is:open label:"type:bug","type:security"`,
**sort by Severity ↓ then Priority** (these are now real single-select **fields** on
this board — see note below — so you can sort/group on them natively).
**KB — Research & Eval (#10):** Table, `is:open label:"type:research","type:eval"`,
group by **Labels** (type) or by **Phase**; the board also has Priority/Severity/Phase fields.
**KB — Critical Path (#8):** Board, manual membership (the agent added the 9 gate
items #325/#326/#328/#275/#342 + #107/#80/#262/#253), group by Status.

> **Fields on #9 and #10:** `Priority`, `Severity`, and `Phase` single-select
> fields exist on both boards (run by `scripts/sync-board-fields.sh`), populated
> from each issue's `priority:`/`severity:` labels and Main's `Phase`. They re-sync
> on any `migrate-issues.sh` re-run. Note: Projects fields are **per-board**, so
> these are independent copies of Main's — the **labels remain the source of
> truth**; the fields are a sort/group convenience. (Severity is sparse outside
> super-qa issues — by design, severity is a super-qa-only dimension.)

## 3. PAT for the auto-add workflow

The `add-to-project.yml` workflow (already wired to projects #7/#9/#10) needs a
**classic Personal Access Token** with **`repo` + `project`** scope — the default
`GITHUB_TOKEN` can't write user-level Projects v2.

1. Create it: GitHub → Settings → Developer settings → Tokens (classic) →
   Generate, scopes `repo` + `project`.
2. Add as repo secret:
   ```bash
   gh secret set KB_PROJECT_TOKEN -R dutiona/knowledge-base
   # paste the token when prompted
   ```

## 4. Merge the PR → workflows go live

Workflows only run from the **default branch**, so auto-add activates when
[PR #365](https://github.com/dutiona/knowledge-base/pull/365) merges to `master`.
After merge, open a throwaway issue with a `type:bug` label to confirm it lands
on both KB — Main and KB — Bug & Security Triage, then close it.

---

Everything else (labels, projects, fields, the ~108-issue migration) is done by
the agent. Track migration end-state in the PR.

Altitude report SHA-256: c87031c738693b20a877d014305af1b9d348f86dccc547e3bbd9f705de28286e

# Review: build(container): build the project without network, after a fetch-only step (#600)

Reviewer: clean-slate Claude subagent (Opus), no prior context, read-only. Recorded verbatim below; only the repository
snapshot's absolute location is shortened to "the snapshot".

## Verdict

**Not LGTM yet.** The core of the plan is sound. The fetch installs only wheels, every step that runs dependency code is offline, the backend is hash-checked, and D2's catch about `.pth` files under `--compile-bytecode` is real and well measured. Four things need fixing first:

- **One design question for Michaël (HIGH):** D1 never considers uv's bundled backend, which would remove the third-party build backend altogether instead of locking it.
- **Three plan fixes (MEDIUM):**
  - Item 2's static test passes without checking anything on the regressions it is meant to catch.
  - Item 2's mutation check will not fail the way the plan says it will.
  - The review files go into a public repository with no scrub step.

Confidence:
- **High** on the MEDIUM findings. They come from the plan's own text, the repository snapshot and `uv help sync` / `uv help build` (uv 0.13.7).
- **Medium** on how H1 behaves at runtime. uv documents that behaviour, but I did not measure it here.

## Altitude and duplication (assessed first)

I checked the report's numbers and they hold: 261 lines, 21 611 bytes, 3 top-level tasks, and the issue body's SHA-256 matches `1e5e8720…be197d`.

**I accept the report's "proceed" decision.** The plan is longer than the code it describes, and that is justified:
- The block of measured facts is evidence for the three departures from the issue (D1 to D3).
- Verification is real work. What is being protected (a build step's network namespace) can only be observed in real builds.
- No file is restated in full, and no validator is defined.

Two parts come close to a transcript, but they are expected-output assertions, so they are acceptable:
- V3's expected sequence of steps.
- The #585 statement, written in advance for the PR.

The network split is explained three times: in the header, in the step comments and in the guide. A4 requires two of these, and every comment states a non-obvious *why*, so this is acceptable too.

If H1 is adopted, item 1 disappears and most of item 2 shrinks.

## Findings

### [HIGH] H1: D1 does not consider uv's bundled `uv_build` backend, which removes the third-party backend instead of locking it

**Evidence.**
- uv 0.13.7, `--require-build-hashes` help: "No hash is required when uv uses its bundled `uv_build` backend, since it is part of the uv executable."
- uv docs, "Build backend → Bundled build backend": "the `uv` executable also includes a copy of the build backend, which will be used during builds performed by uv, e.g., during `uv build`, if its version is compatible with the `uv_build` requirement. If it's not compatible, a compatible version of the `uv_build` package will be used."
- The project fits uv_build's limits:
  - it is pure Python;
  - it has one root module, `src/knowledge_base`;
  - there are no non-`.py` files under `src/`;
  - there is no `[tool.hatch]` configuration.

**What it removes:**
- All of item 1: the `build` group, the four added lock packages, the equality test, and "the floor is written twice".
- `/backend`, `/dist`, and the `uv build` and `uv pip install` lines.
- D2's ordering constraint. After `COPY src`, one offline step would likely be enough: `RUN --network=none uv sync --locked --offline --no-build --no-dev --group reranker --no-editable --compile-bytecode`. That one step builds the project with the bundled backend (per `--no-build` help, first-party packages are still built), installs it, and compiles everything. This needs measuring.
- D1's "Not covered" gap: a developer's `uv build` would also use the bundled backend.

What the image then trusts is uv, and uv already runs the networked fetch.

**Costs for Michaël to weigh:**
- **(a)** It replaces #574's `hatchling>=1.27`, which #574 chose as "a contract, not a pin". That is a past decision.
- **(b)** It ties the build to uv's version.
  - uv recommends an upper bound, `uv_build>=X,<0.14`.
  - With `ghcr.io/astral-sh/uv:latest` floating (also #574's choice), the first uv 0.14 image makes the bundled copy incompatible. The offline step then fails loudly until someone bumps the bound.
  - The alternative is a bare minimum. That goes against uv's advice but matches "ranges or minimums".
- **(c)** It departs from A1's literal wording ("`--no-build` on both commands", "installs with `--no-deps`"), so it needs its own D-entry.
- **(d)** `CLAUDE.md` says "hatchling build" and would need updating.

**Recommendation:** decide this before item 1. If hatchling stays, D1 should record why uv_build was rejected.

### [MEDIUM] M1: Item 2's static test checks how flags are spelled, not the property, and passes without checking anything on the regressions it exists for

The Testing section says "an image that loses any of these still builds, so only a check notices". That makes this test the only guard, and as written it has these holes:

- **A networked build-stage `RUN` with no `uv` command passes.** For example, `RUN /app/.venv/bin/python -c …` placed before `COPY src`. The rule only constrains "each of its `&&`-separated `uv` commands", so with no `uv` command there is nothing to check.
- **Compilation can come back through an equivalent setting.** `ENV UV_COMPILE_BYTECODE=1` is the environment equivalent shown in `uv help sync` (`[env: UV_COMPILE_BYTECODE=]`). I believe it is also the idiom in uv's Docker guide. A `[tool.uv] compile-bytecode = true` key (I think that is a valid setting) does the same. Either one turns compilation back on in the networked fetch, and the test still passes.
- **`--no-install-project` is not pinned, and it is the flag that matters for the project.** `--no-build` help says: "First-party packages, such as projects in the workspace, will still be built." If someone drops `--no-install-project` from the first fetch command, uv fetches an unlocked hatchling into build isolation and runs it with network. The build does fail afterwards, because `src/` is missing, but only after that code has run.
- **A1's `--offline` and `--no-deps` clauses are not checked.** The Acceptance map credits them to "item 2's test", which checks neither.

**Fix:** write the test as an allowlist instead. It costs about the same:
- Every networked build-stage `RUN` except the marker check consists only of `&&`-joined `uv sync` commands. An optional `UV_PROJECT_ENVIRONMENT=` prefix is allowed.
- Each of those commands has `--locked`, `--no-build`, and either `--no-install-project` or `--only-group`, and none has `--compile-bytecode`.
- `UV_COMPILE_BYTECODE` appears nowhere in the build stage, and `[tool.uv]` has no `compile-bytecode` key.
- Every `uv` command in the offline steps carries `--offline`, and `uv pip install` carries `--no-deps`.

### [MEDIUM] M2: Item 2's mutation check will not fail as written, and the "stays the first `RUN`" constraint is not enforced

Item 2 Green says: "temporarily moving the fetch `RUN` above `COPY .archive-commit ./` fails the `_archive_check` tests". It won't:
- Moving only the `RUN` moves no `COPY`. The tightened `_archive_check()` ("the check precedes every build-context `COPY` but the marker's") still holds.
- The network test also passes, because the fetch still comes before `COPY src`.

So the test run will report 5 passed, contradicting the plan's expectation. The Constraints line "The `.archive-commit` check (#585) stays the first `RUN`" has no test behind it.

**Fix:**
- Assert that the marker check is the first `RUN` of the build stage. That is one assertion, and it matches the Constraint.
- Mutate the realistic hoist instead: move `COPY pyproject.toml … ./` together with the fetch above the marker.
- Make the "build-context `COPY`" rule explicitly skip `COPY --from=`, because the uv `COPY --from=…` line comes before the check.

### [MEDIUM] M3: Operations 2 puts review files into a public repository's history, and onto a public plan issue, with no scrub step

The Constraints forbid "home paths, host names, tailnet names, addresses or interface names in the plan, commits, PR or docs". But reviewer outputs repeat what their prompts gave them, and this review's own prompt carried absolute home-directory paths.

"`finish-pr` Item 5 removes it from the branch before merge" does not unpublish anything:
- GitHub keeps a pull request's head commits fetchable after a squash merge (`refs/pull/<n>/head`).
- The plan issue is public.

**Fix:** in Operations 2, before the plan commit and before the archiver runs, add a grep over the plan and every review file for home-directory prefixes, host and tailnet names, addresses and interface names. Redact anything it finds.

### [LOW] L1: The `.pth` probe covers one of the fetch step's two commands

The Outcome says the fetch "starts no Python in an environment it has filled". The second command, `UV_PROJECT_ENVIRONMENT=/backend uv sync …`, runs after `/app/.venv` has been filled. I expect uv to create `/backend` from the system interpreter without starting `/app/.venv/bin/python`: the venv is not on `PATH` and `VIRTUAL_ENV` is unset. So this is likely fine. Since it is the step's whole security claim, re-run the probe against the final two-command line. (Moot if H1 is adopted.)

### [LOW] L2: The guide paragraph in item 3 says something false

"Every later step starts with `RUN --network=none`" is not true of the runtime stage's `RUN groupadd …`. That step comes later in the file and keeps network. The header's wording is correct ("Every step that runs dependency code … starts with RUN --network=none"). Reuse it, or write "every later build-stage step".

### [LOW] L3: D2 writes the runtime package selection twice

The selection (`--no-dev --group reranker`) appears in both the fetch and the compile `uv sync`. If a later edit adds a group to the fetch only, the compile sync runs in exact mode and silently uninstalls that group. The merge gate would not see it, because V4 runs only as PR evidence.

**Fix:** add `--inexact` to the compile sync. Per uv's help, a sync still compiles "the entire site-packages directory", so nothing is lost, and the mismatch becomes harmless. Alternatively, have M1's test assert that both commands use the same selection flags.

### [LOW] L4: Verification details

- **V2:** uv usually prints both the backend's traceback and its own error summary, so the canary text may appear twice. Expect "a `netcanary:` line reporting 1", not "one … line".
- **V4:** a plain `diff` also prints a hunk header. Name the exact form, for example `diff … | grep '^[<>]'`, so that "prints exactly `> knowledge-base==0.1.0`" can be checked.
- **V6:** "`HEAD~1` (item 2's commit)" holds only on the first pass. After a review fix to a `RUN` or `COPY` line, the plan says to repeat V6. By then `HEAD~1` differs in `Containerfile`, so the `git diff --quiet` guard stops V6 and nothing says what to do next. Name the rule: use the newest earlier commit whose build inputs match HEAD's, or a throwaway empty commit in a scratch clone.
- **Item 1 Green:** `grep -c` exits 1 when it prints `0`. Under the plan's `echo "exit=$?"` convention, that reads as a failure. Say that exit 1 is expected there.

### [LOW] L5: Minor

- **Redundant flag.** `--no-install-project` on the `--only-group build` command does nothing extra. uv's help says that with `--only-group`, "The project and its dependencies will be omitted." Keep it only if M1's allowlist wants a single spelling.
- **Unneeded skip condition.** The new static tests inherit the module-level skip when there is no git checkout, although they only read files. This is harmless in the merge gate, because a worktree's `.git` file exists. If it is cheap, limit that skip to the tests that actually need git.
- **D1 can cite a stronger reason.** It could name uv's own reason the in-pyproject variant fails: hashes in `tool.uv.build-constraint-dependencies` are "not checked" "when build isolation is disabled", and isolation would need network in the build step.

## Structural completeness

The Documentation, Testing and Verification sections are all present and substantive.

Documentation lists every place that describes the image build, and correctly leaves `README.md`, `AGENTS.md` and `docs/usage/searching.md` unchanged. Testing gives honest reasons for what it covers and what it doesn't. If H1 is adopted, `CLAUDE.md`'s "hatchling build" line needs updating.

## Feasibility

The steps work in the stated order:
- **Dependencies:** item 2 needs item 1's group, and every commit builds.
- **Red/green counts:** they add up. The module has 3 tests today, 4 after item 1 and 5 after item 2. Today's `Containerfile` fails only the new network test, while the tightened archive check passes.
- **Line length:** every product line in the fenced blocks and the guide quote stays at or under 120 columns once the plan's indentation is removed (longest: 117).
- **`--no-build` works:**
  - In `uv.lock`, the project is the only package without wheels.
  - The only other package lacking a pure or linux-aarch64 wheel is the Windows-only `pywin32`, so arm64 builders are fine as well.
  - hatchling is not in `uv.lock` today.

The rest of what I checked holds:
- uv's help confirms that `uv pip install --compile-bytecode` compiles only the files it installs, while a sync compiles all of site-packages. This supports D2 and its ordering.
- The A1 to A5 map is complete, apart from the coverage gap in M1.

## Resolution

- [HIGH] H1, uv_build not considered → D1 now records it as a rejected option with its reasons: it reopens #574's
  backend choice for every developer build, and the backend becomes the copy inside the floating `uv:latest` (with an
  upper bound each uv minor breaks the build; with a bare minimum the wheel's producer floats with uv, less
  reproducible than a locked hatchling). It is the smaller trusted base only together with pinning the uv image, which
  is a separate owner decision. The option is raised to Michaël at plan approval, framed with those consequences.
- [MEDIUM] M1, test checks spelling only → item 2's test is now an allowlist: networked build-stage `RUN`s are only
  `&&`-joined `uv sync` commands (optional `UV_PROJECT_ENVIRONMENT=`) with `--locked`, `--no-build` and
  `--no-install-project` or `--only-group`, none compiling; no `UV_COMPILE_BYTECODE` in the build stage and no
  `[tool.uv] compile-bytecode`; every offline uv command carries `--offline` and the install `--no-deps`. Codex raised
  the same gap independently.
- [MEDIUM] M2, mutation check would not fail → `_archive_check` now also asserts the check is the build stage's first
  `RUN` and skips `COPY --from=`; the mutation moves `COPY pyproject.toml …` together with the fetch.
- [MEDIUM] M3, public review files → Operations 2 now greps the plan, altitude report and every review file for home
  paths, state paths, tailnet names and tailnet addresses before the commit and before the archiver, and redacts. This
  file shortens the snapshot's absolute location.
- [LOW] L1, `.pth` probe on the second command → measured (host uv 0.13.7, fresh cache): the `/backend` sync with
  `--only-group build` did not start the filled venv's Python and installed no project. Recorded in the measured facts.
- [LOW] L2, guide sentence false for the runtime stage → "every later build-stage step".
- [LOW] L3, selection written twice → `--inexact` added to the offline compile sync; measured that it still compiles an
  untouched package's module.
- [LOW] L4 → V2 expects the message, possibly repeated; V4 names `diff … | grep '^[<>]'`; V6 uses an empty commit in a
  scratch clone, valid after any review fix; item 1's green uses `git diff --numstat` (`<n> 0`).
- [LOW] L5 → `--no-install-project` dropped from the `--only-group build` command (measured: no project installed);
  the git skip moves to the two tests that run git; D1 cites that build-constraint hashes are checked only in build
  isolation.

---

# Round 2 (revision 2, delta review)

Altitude report SHA-256: ad91780d0304d907b0e227b41dbf16e3012230304b9c3d67b5a20afc16f0ed14

Verdict: not yet LGTM; the round-1 fixes to the test, the mutation check, the guide, D2 and Verification are correct.
Accepted the altitude report's "proceed" (the 37 added lines trace to findings). H1 (uv_build): disposition accepted,
not blocking; the choice is an owner call, once one sentence of the record is corrected. M1, M2, L1 to L5 resolved;
M3 partially.

- [MEDIUM] R2-M1: Operations 2's scrub can never print nothing (the pattern matches the scrub line itself and the
  Constraints' wording) and misses the real identifiers (host names, interface names, other private ranges, other
  absolute paths). Fix: collect the host's real values into a pattern file outside the worktree and `grep -nF -f` it.
- [MEDIUM] R2-M2: D1 says uv_build is "the smaller trusted base only together with pinning the uv image"; it is the
  smaller trusted base now (uv alone, which the fetch already trusts); pinning adds reproducibility, not less trust.
- [LOW] R2-L1: the allowlist is only as strong as its command splitter (`;`, `|`, `$(…)`, backticks, redirections);
  also refuse `--python`/`--active` in networked syncs, which would start the filled venv's interpreter.
- [LOW] R2-L2: name both existing tests as the ones keeping the git/sh/checkout skip; say how many tests the mutation
  fails depending on whether the new test calls `_archive_check()`.
- [LOW] R2-L3: V4's `grep -c` exits 1 on success; use `! grep -q`.
- [LOW] R2-L4: item 3's quote has a leftover wrap; V6's scratch commit should use a Conventional message and check out
  HEAD's sha explicitly.

## Resolution (round 2)

- [MEDIUM] R2-M1 → Operations 2 now builds a pattern file outside the worktree from `$HOME`, `hostname`,
  `hostname -f`, the VPN's own host and domain names, every interface name but `lo` and every non-loopback address,
  and runs `grep -nwF -f` over the plan, altitude report and every review file. Run before revision 3 was dispatched:
  72 identifiers, no match (exit 1). Codex raised the self-match independently (MEDIUM).
- [MEDIUM] R2-M2 → D1's sentence replaced with the reviewer's correct statement of the trade.
- [LOW] R2-L1 → the allowlist refuses `;`, `|`, `$`, backtick, `<`, `>` in a networked `RUN`, requires every token
  after `uv sync` to be an option or its value, and refuses `--python` and `--active`.
- [LOW] R2-L2 → both existing tests named; the mutation fails the three test cases that call `_archive_check()`, and
  the new network test finds the marker check by its text.
- [LOW] R2-L3 → `! grep -q '^hatchling=='`.
- [LOW] R2-L4 → quote rewrapped at 120; V6 detaches at HEAD's sha and commits `chore: probe`.
- Codex's round-2 LOW ("Every later build-stage step" includes `COPY src`) → "Every later build-stage `RUN`".

---

# Round 3 (revision 3, delta review)

Altitude report SHA-256: eb308ea8264a338ef0ded552ac54a875df2771b2358f4e64202de3826e67bd86

Verdict: LGTM after one MEDIUM fix. Every round-2 finding fixed correctly, and Codex's round-2 LOW too. Accepted the
altitude report's "proceed" (10 lines of growth, all from findings).

- [MEDIUM] R3-M1: the files to commit are undefined, and the review tree's two delta diffs carry an absolute home path
  in their `---`/`+++` headers; the scrub did not cover them. Fix: list exactly the committed files, say the diffs,
  the last-reviewed copy and the issue-body copy are never committed, and scrub exactly the staged files.
- [LOW] R3-L1: the option rule is still a denylist; uv 0.13.7 accepts `uv sync --compile` (an alias of
  `--compile-bytecode`) and `-p` (short for `--python`), and `--python=…` would slip past an exact-token check. Fix:
  an exact allowlist of `--locked`, `--no-build`, `--no-dev`, `--no-install-project`, `--group <name>`,
  `--only-group <name>`, which also covers a lone `&` and parentheses.
- [LOW] R3-L2: the scrub leaves out the VPN's peer names; add them from the VPN client's status listing.
- [LOW] R3-L3: D1's `uv_build` entry states its cost twice; keep one.

## Resolution (round 3)

- [MEDIUM] R3-M1 → Operations 2 names the four committed files (plan, altitude report, advisor file, this file) and
  says the drafts directory, the review tree's copies and diffs and the external reviewers' raw outputs are never
  committed; the scrub runs over exactly the staged files. The review tree sits outside the worktree, so its diffs
  could not have been staged, but the rule is now explicit.
- [LOW] R3-L1 → the networked `uv sync` tokens are an exact allowlist (`--locked`, `--no-build`, `--no-dev`,
  `--no-install-project`, `--group <name>`, `--only-group <name>`, the `UV_PROJECT_ENVIRONMENT=<path>` prefix), which
  refuses `--compile`, `-p`, `--python=…`, `--active` and every shell metacharacter by construction.
- [LOW] R3-L2 → the pattern file adds the VPN peers' host names, DNS names and addresses (96 identifiers). Rerun over
  the four files: the only matches sit inside `<owner>/<repo>` references to the public GitHub owner, which
  Operations 2 now exempts explicitly; no other match.
- [LOW] R3-L3 → D1 keeps one statement of the cost: "pinning the uv image would remove those costs but is #574's
  decision".

---

# Round 4 (revision 4, delta review)

Altitude report SHA-256: 86b88f4e7eaa0f0dcd49a29620d29e279285c260807004693c52ef880ad1e108

Verdict: LGTM. All four round-3 findings fixed correctly; nothing at BLOCKER, HIGH or MEDIUM. Accepted the altitude
report's "proceed" (3 lines of growth, all from findings).

- [LOW] R4-L1: the allowlist's value slots (`<name>`, `<path>`) need a character set, or `--group reranker;python` or
  `UV_PROJECT_ENVIRONMENT=$(…)` would pass a whitespace-split check. Codex raised the same point as MEDIUM.
- [LOW] R4-L2: apply the `<owner>/<repo>` exemption per match (delete the references, then grep), never by dropping
  whole lines, or a line holding both a reference and a real leak would be hidden.

## Resolution (round 4)

- [LOW] R4-L1, and Codex's round-4 MEDIUM → each `<name>` and `<path>` must be a literal matching `[A-Za-z0-9._/-]+`.
- [LOW] R4-L2 → Operations 2 deletes `<owner>/<repo>` references per match with `sed -E` before `grep -nwF -f`; rerun
  that way over the four files: 0 hits.

---

# Round 5 (revision 5, confirmation review)

Altitude report SHA-256: e1a11d7968210f5b75d296c386ec09365a50343078c267b942a6cae8c24d000c

Verdict: LGTM. Both round-4 LOWs and Codex's round-4 MEDIUM fixed correctly (checked the `sed` expression with GNU sed:
it strips the reference, keeps the issue number and drops no line). Accepted the altitude report's "proceed".

- [LOW] R5-L1: the allowlist checks each networked command on its own and does not stop a second networked `uv sync`
  into an environment that already holds packages (a second fetch `RUN`, `UV_PROJECT_ENVIRONMENT=/app/.venv`, or
  `/backend` twice); uv likely queries an existing environment's interpreter, whose `site` runs the `.pth` files with
  network. Fix: exactly one networked `RUN` besides the marker check, its syncs targeting distinct environments, none
  prefixed `/app/.venv`. Optional: confirm the premise with the `.pth` probe.

## Resolution (round 5)

- [LOW] R5-L1 → premise measured (host uv 0.13.7): a second `uv sync --no-install-project`, no `--compile-bytecode`,
  fresh cache, into the already-filled venv ran the `.pth`. Item 2's test now requires exactly one networked `RUN`
  besides the marker check, whose syncs target distinct new environments (at most one unprefixed, each prefix path
  once, never `/app/.venv`); the measured fact is recorded in the plan.

---

# Round 6 (revision 6, confirmation review)

Altitude report SHA-256: efa4684d91193ea7562a191bf8421277ea5b6363007425c936e8a15eca213853

Verdict: LGTM. R5-L1's fix is correct; nothing at BLOCKER, HIGH or MEDIUM. Accepted the altitude report's "proceed".
Recommended no further round for the three LOWs, and noted that every round since round 3 found a narrower bypass of
the same token parser.

- [LOW] R6-L1: an `ENV UV_PROJECT_ENVIRONMENT=` in the build stage would make both fetch commands target one
  environment while the test passes. Fix: no build-stage `ENV`/`ARG` sets a `UV_*` variable other than the existing
  `UV_PYTHON_DOWNLOADS`.
- [LOW] R6-L2: prefix paths compared as strings (`/app/.venv/`, `/app//.venv`, a relative `.venv`) let one
  environment pass as two. Fix: require absolute, normalized paths.
- [LOW, optional] R6-L3: assert the single networked fetch `RUN` as one exact string (continuations joined,
  whitespace collapsed) instead of parsing it, keeping the `UV_*` rule, the `[tool.uv]` check, the first-`RUN` and
  offline-flag checks; no parser, no class of bypasses; a change to the fetch line becomes a deliberate test edit,
  which is the point. If adopted, R6-L2 is moot.

## Resolution (round 6)

- [LOW, optional] R6-L3 → adopted. Item 2's test asserts the networked build-stage `RUN`s are exactly the marker check
  and then the exact fetch line, before `COPY src ./src`; the token allowlist, value grammar and distinct-environment
  rule are gone. The plan is shorter for it (318 → 313 lines). The mutation now fails the three `_archive_check()`
  cases and the network test (order).
- [LOW] R6-L1 → adopted: no build-stage `ENV` or `ARG` sets a `UV_*` variable other than `UV_PYTHON_DOWNLOADS`; with
  the `[tool.uv] compile-bytecode` check, these are the ways to change what the exact line does without editing it.
- [LOW] R6-L2 → moot under R6-L3 (no path is parsed).

---

# Round 7 (revision 7)

Altitude report SHA-256: fedb92c1aa4165a8108a44581d8cdcbbd4d9ad5753d4bd7c61599698f126648f

Verdict: LGTM. Checked mechanically that the exact string equals item 2's fetch block once continuations are joined
and whitespace collapsed, and that the exact line implies every rule the parser enforced; the `UV_*` ban keeps the
"unprefixed sync creates `/app/.venv`" assumption true. Red still 1 failed, 4 passed; the mutation fails 4 tests.
Accepted the altitude report's "proceed".

- [LOW] R7-L1: two edits still change what the exact fetch line does without touching it: a non-`UV_*` `ENV` (a
  build-stage `PATH` with `/app/.venv/bin` first would likely make the `/backend` sync start the filled venv's
  interpreter) or a `SHELL`; and a `RUN --network=none` before the fetch that writes a uv config file with
  `compile-bytecode = true`. Fix: require the build-stage instructions before the fetch, `FROM` aside, to be an exact
  sequence (uv `COPY --from`, `ENV UV_PYTHON_DOWNLOADS=never`, `WORKDIR /app`, the marker `COPY` and check, the
  `COPY pyproject.toml …`, the fetch), replacing both the two-networked-`RUN`s rule and the `UV_*` rule.

## Resolution (round 7)

- [LOW] R7-L1 → adopted: item 2's test pins the build-stage instructions up to and including the fetch, `FROM` aside,
  as that exact sequence, and requires every later build-stage instruction to be `COPY src ./src` or a
  `RUN --network=none ` (the offline compile sits between the fetch and `COPY src`, so the sequence ends at the fetch,
  not at `COPY src`). The `[tool.uv] compile-bytecode` check, the offline-flag checks and `_archive_check()`'s #585
  assertions stay; the `UV_*` rule is subsumed and removed.

---

# Round 8 (revision 8)

Altitude report SHA-256: 44b0401c4fe1a026aabcf2e2c69b10bed3a7abcecc8a09c60061c4d714dbfaba

Verdict: LGTM. R7-L1 fixed correctly; ending the exact sequence at the fetch (not at `COPY src`) is right, because
the offline compile `RUN` sits between them, and it is stricter than the suggested wording. The pinned prefix also
keeps a project-level `uv.toml` and a `[tool.uv.sources]` editable path dependency out of `/app` before the fetch.
Red 1 failed, 4 passed; the mutation fails 4 tests. Accepted the altitude report's "proceed". Neither note needs
another round.

- [LOW] R8-L1: "the marker check" is listed by role; if the test identifies it as `_archive_check()` does (the single
  `RUN` mentioning `.archive-commit`), its body is unpinned, and an edit appending a home-directory uv config write to
  it would run with network before the fetch. Pin it exactly, or accept the risk explicitly; the reviewer leans to
  accepting it.
- [LOW] R8-L2: the altitude report's line 17 is 125 columns.

## Resolution (round 8)

- [LOW] R8-L1 → accepted as a stated boundary, not pinned: the test identifies the marker check by its text, as
  `_archive_check()` does, and #585's behavioural tests run its body. A config write appended to it is not a plausible
  accident: it is a deliberate edit to #585's own check, which review sees. Pinning it would copy the check's long
  refusal message into a second test. The residue of the static guard is the base image plus that one line.
- [LOW] R8-L2 → fixed in the revision-9 report, which had to be refreshed anyway: recording the Super evidence
  requires Codex's verdicts as `…-external-codex.md`, so Operations 2 now names five committed files.

---

# Round 9 (revision 9, final confirmation)

Altitude report SHA-256: 7b31829ae1e3ede63fc98507e08424f55c06555750f4d612a2dd22f0ad1a1113

Verdict: LGTM. The delta changes only Operations 2 and is consistent: five committed files named the same way
throughout, all five scrubbed; the exclusions now draw the line at raw transcripts and logs; the scrub and redaction
steps are unchanged word for word. R8-L2 fixed (no report line over 120). Accepted the altitude report's "proceed".

- [LOW] R9-L1: the R8-L1 boundary (the marker check is matched by its `.archive-commit` text, not pinned; the static
  guard's residue is the base image and that line) is recorded only in this file, so the Testing summary's "an exact
  instruction sequence" overclaims slightly. Fix: one clause in item 2's first bullet and in the Testing sentence.

## Resolution (round 9)

- [LOW] R9-L1 → arbitrated, plan bytes left unchanged so both reviewers' final verdicts stay bound to them: the
  boundary is stated here and in "Resolution (round 8)", and the implementation states it where the implementer and
  the next editor read it, in `test_dependency_code_runs_without_network`'s docstring and in the PR's Test plan. The
  plan audit at `finish-pr` checks that both say it. Both reviewers' final verdicts on revision 9: LGTM.

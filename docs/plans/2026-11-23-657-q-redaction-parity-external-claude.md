Reviewer: Claude Sonnet (clean-slate subagent, medium effort) via Agent tool, standing in for Codex (degraded: quota out until ~2026-11-25); rounds 1 to 3

~~~~
# Review of plan: #657 group Q

## Round 1

Altitude report SHA-256: e3032065434cdc2ebba54dfa22a1ee1b2017e719b7a0a53665aef3b8b0834c2b

### Altitude and duplication (first)

The altitude report's disposition (proceed) holds. The plan is decision-heavy and does not embed product code. The only literal volume is the two gate command lists, copied from the AGENTS.md files. The three-issue scope, ME-first ordering and local-agent bundling are owner decisions, so I treat them as justified overrides and do not challenge them.

Duplication and simplicity checks:
- D1 (new table) is smaller than the rejected nullable-target rebuild. Agreed.
- D2 (seam at `_build_provider` plus a fresh `_provider_cache`) is the least production code: no hooks are added. Agreed.
- D4 (one walk, key through the same leaf) adds no abstraction. But it carries a parity decision that is not flagged (see H1).
- Items 3, 7 and 9 are boy-scout commits, each separate as the global rules require. Item 9 sits inside a "parity" PR, but it is also the prerequisite for the item 10 report test, so it is justified.

### Findings

### [HIGH] H1. Keys go through `_metadata_leaf`, a deliberate divergence from ME, not listed in "For Michaël" (D4, item 10; `ingest.py:156-158`)

ME scrubs a key as a plain string: `scrub_with(&key, key_denylist)`, at `mod.rs:889-893`. The plan routes a key through KB's `_metadata_leaf`. A whole-URL key then loses only its userinfo and keeps its query string, where ME would run the full scrub on it.

The owner ruling that justifies this for values ("a whole URL is a stored reference") does not obviously apply to keys. A URL used as an object key is not a dereferenceable reference. A credential in its query (`?token=...`) would survive in KB and be redacted in ME. Redaction parity is the security boundary, so this should not be decided silently by the planner.

Recommendation: key the member through plain `scrub`, which is the ME rule, and gives the walk simpler code (the leaf is not applied to keys). Or list this as an explicit decision in "For Michaël" with the query-credential consequence stated. Add a test either way. Before choosing, confirm that `scrub` handles a URL key's userinfo; I did not verify that.

### [MEDIUM] M1. The #654 bundle rule can silently change a test's meaning (item 6.5)

The rule converts every `@patch(..., _fake_embed)` to `usefixtures("stub_document_embedder")` and "deletes a module fake nothing uses". It never requires checking that the module's `_fake_embed` is behaviourally the default.

- The ~15 `_fake_embed` definitions are mostly `[[0.1]*dim ...]`; the sampled ones in test_search.py and test_conclusions.py are that.
- A local model applying the rule mechanically will not notice a fake that returns distinct vectors, `None`, or the wrong dimension.
- The "same ids and `-rA` outcomes" evidence will not catch a test that still passes but now asserts less (for example, ranking tests that depended on distinct vectors).

Add to the rule: before converting a file, the worker diffs each fake's body against the fixture's default and lists any that differ. That list goes in the 6.2 ledger.

### [MEDIUM] M2. Item 6 verification skips `slow` tests

The baseline and the green check are `-m "not slow"`. Files that carry `slow` tests (for example `test_ingest.py`) have their decorators rewritten, but those tests' outcomes are never compared; collect-only covers only their ids. Either run the touched files' slow tests once before and after, or state in the plan that no slow test uses these patches.

### [MEDIUM] M3. The fake provider has no `token_counter` (D2; `embeddings.py:584`)

`split_inputs` casts `get_provider(cfg)` to `OpenAICompatProvider` and calls `.token_counter()` whenever `cfg.max_input_tokens` is set. Today the real provider is still resolved, because only `embed` is patched. Under D2 the fake answers instead, so any test that sets `KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS` and also uses `stub_document_embedder` will raise `AttributeError`.

- Those tests should all be in `test_embed_limits`, `test_embed_identity`, `test_embeddings` and `test_settings`, which D2 says use `bounded_embedder`.
- The plan should state this explicitly and name the failure mode, so a worker does not paper over it.

### [MEDIUM] M4. Query path now flows through the fake (D3, item 6.2)

After `embed_query` calls `get_provider(...).embed` itself, any test that has `stub_document_embedder` but not `stub_embedder` and searches now records `instruction + text` into the fixture's list and gets the 0.1 vector. 6.2 lists "tests asserting recorded texts", which covers this. Add "search under `stub_document_embedder` without `stub_embedder`" as an explicit candidate class so the grep-by-assertion step does not miss it.

### [MEDIUM] M5. The `web.py` call sites and the web import tests are not named

`web.py:440,769,884` call `_embed_with_config` (imported from `ingest`), so D3 changes their behaviour. Tests patch it by name in `ingest`/`vision`; the plan covers vision but is silent on web's tests. `test_web_limits.py` and `test_web_redirects.py` are in B8 for the `embed` patches. Confirm no test patches `knowledge_base.web._embed_with_config`: I found none in a grep, so one sentence in D3 stating that closes it.

### [MEDIUM] M6. Sequencing ambiguity: does item 5 wait for item 4's merge?

Items are numbered sequentially, and item 4 ends "Michaël merges". Items 5-9 do not need ME's merge; only item 10 does ("after item 4's merge"). Say explicitly that 5-9 proceed while the ME PR is in review. Otherwise the longest wait (ME review plus the merge) sits on the critical path ahead of the 415-site refactor.

### [MEDIUM] M7. "Changed object comes out sorted" is imprecise (D4)

Per the D4 text, `redact_json` rebuilds every object over `sorted(value)`, so it reorders all objects, not only changed ones; `redact_metadata` hides this by returning the stored string when `clean == value`. The effects are:

- A changed parent also sorts its unchanged children.
- ME's output is ordered by the clean key (BTreeMap), KB's by the original key. The stored bytes can differ; semantics do not.

Reword D4 and the docstring, and make sure the new tests compare parsed values, not text.

### [LOW] L1. Patch-site counts are inconsistent

Constraints say "~207 patches"; item 6's Red grep names 235 and 180 sites (415 in total). The two figures count different things (file count and line count versus sites). State one number so "the red grep prints nothing" is checkable.

### [LOW] L2. The KB worktree and branch creation is not a step

Item 2's KB dry run and item 5's baseline need a KB worktree. Only the ME worktree is created (item 1). Add the KB `git worktree add` and lock to item 5 or earlier. The dry run in item 2 also runs before the item 5 baseline; ensure it starts from a clean worktree.

### [LOW] L3. Cascade-only cleanup for `folder_summary_truncations` (D1; `folder_summaries.py:78-81`)

`drop_summary` deletes the summary row and relies on `ON DELETE CASCADE`. `PRAGMA foreign_keys=ON` is set in `get_connection` (`db.py:397`). The migration paths toggle it OFF around rebuilds (`db.py:419-567`). A connection without the pragma would leave an orphan record. Cheap fix: an explicit `DELETE FROM folder_summary_truncations` in `drop_summary`; the test `test_a_dropped_folder_summary_takes_its_truncation_record` then does not depend on the pragma.

### [LOW] L4. Item 3 (ME docs line) is outside "fixture only"

Already flagged to Michaël as For-Michaël item 1 and kept as a separate commit. No action beyond what the plan does.

### Gaps and risks checked and found sound

- ME walk semantics (`mod.rs:807-908`): the value is judged with the key as written, then the key is scrubbed, then the first free `~i` is taken over a map that holds the earlier keys. This matches D4, including ordering by sorted original key. Code-point order equals UTF-8 byte order for valid scalars.
- Idempotence: a second pass over `{"[REDACTED:x]":..., "[REDACTED:x]~1":...}` changes nothing, so `rewrite` converges.
- `redact_metadata` returns the stored string when `clean == value`, so no row is rewritten needlessly.
- D5: three shared cases, collision left to each repository's unit test. A single expected output cannot also be the text pass's output, so the reasoning is right.
- #656 mechanics: `folder_summaries` has `folder_path` as PRIMARY KEY (`db.py:1269`), so the FK is valid. The upsert uses `ON CONFLICT DO UPDATE`, not `INSERT OR REPLACE`, so the cascade is not triggered by a refresh. Promotion deletes and re-records inside its transaction. `embed_truncations`'s exclusion list in `redact_store._merge_into` is unaffected, because the new table has no FK to `chunks`.
- Item 9 (count placeholders added, new minus old) is a good fit for key redaction and removes `_strings`.

### Structural completeness

Documentation, Testing and Verification sections are all present and specific. Docs are enumerated per item. Verification mirrors both AGENTS.md Verify blocks and adds the cross-repository digest check. No gap.

### Feasibility

The order is achievable: ME PR first (in parallel with items 5-9 once M6 is stated), #654 before #656 so both folder writers expose cuts, #652 last. The one hard dependency is item 10 on ME's merge, and it is correctly placed last. The 415-site bundle conversion is the main schedule risk; M1 and M2 are the cheapest mitigations.


## Round 2

Altitude report SHA-256: 55af43d4b97ea2ccf10680a6ce2273c2684fcfec5126bf7f978b796b2466ce06

Altitude: the refreshed report is consistent with the delta (plan grows by 20 lines, all decisions and evidence; no embedded code). No altitude concern.

Round-1 findings re-checked against the code:
- H1 resolved. D4 now scrubs keys with `scrub`, which is ME's rule (`mod.rs:889-893`). The delta no longer needs `_metadata_leaf` for keys, so the walk is simpler. The resolution's note that links are exempt from every rule but url-credential (`redact.py:5,167`) is accurate; the plan now says so and surfaces the choice to Michaël (item 4). The whole-URL-key test expectation (password redacted, user kept) follows from that rule.
- M1, M4 resolved by 6.2 and the 6.5 rule ("only where the fake equals the default").
- M2 resolved and verified: `mark.slow` appears only in `test_pymupdf4llm_integration.py` and `test_vision_omniparser_integration.py`, neither of which uses the patches.
- M3 resolved and verified: `conftest.py:112` deletes `KNOWLEDGE_BASE_EMBED_MAX_INPUT_TOKENS` per test, so only tests that set it themselves meet the missing `token_counter`.
- M5, M6, M7, L1, L2 resolved as described.
- L3 rejection accepted: `get_connection` sets `PRAGMA foreign_keys=ON` (`db.py:397`) and `embed_truncations`/`embed_refusals` rely on the same cascade, so an explicit delete would be defensive code. No new evidence to the contrary.

New findings: none.

LGTM. The delta answers every round-1 point with code-checked reasons and adds no new risk.


## Round 3

Altitude report SHA-256: 26990ed5788a07445312c918ebeab0b8f3262a9300f456dab533f17523f64157

Delta reviewed: two sentences, the Outcome ("a key is scrubbed as a string") and the Testing line ("keys never reaching a custom leaf"). Both now agree with D4 and with item 10 (lines 70-74, 231-232 of the plan). No other `leaf` wording contradicts D4. No new findings.

LGTM. A pure consistency fix, with no behaviour or scope change.

REVIEW COMPLETE
~~~~

## Resolution

Round 1 (plan revision 1, SHA-256 d2d254c3…), each finding mapped to revision 2 (SHA-256 8fd0fed9…):

- H1 (keys through `_metadata_leaf` diverge from ME): accepted. D4 now scrubs a key with `scrub`, never the caller's
  leaf, as ME's `scrub_members` does; item 10's whole-URL key test expects the password redacted and the user kept;
  `test_the_key_rule_runs_before_a_custom_leaf` stays and gains a token-key assertion; surfaced as item 4 of "For
  Michaël". The finding's query-credential example does not hold (links are exempt from every rule but url-credential
  in both engines); the real difference is userinfo and machine tokens, which the plan now states.
- M1 (bundle rule can change a test's meaning): accepted; 6.2 lists every module fake that is not the default answer,
  and 6.5 applies the decorator rule only to fakes equal to the default.
- M2 (slow tests not compared): accepted; verified no `slow`-marked test uses these patches, stated in item 6.
- M3 (fake has no `token_counter`): accepted; D2 names the AttributeError and that only the limit tests declare a limit.
- M4 (queries now reach the fake): accepted; 6.2 adds tests that search without `stub_embedder` as a candidate class.
- M5 (web call sites): accepted; D3 states web.py's calls change and no test patches `web._embed_with_config`.
- M6 (sequencing): accepted; items 5 to 9 proceed while the ME PR is in review.
- M7 (ordering wording): accepted; D4 reworded, tests compare parsed values.
- L1 (counts): accepted; item 6 gives 235 and 180 sites and why the issue said ~207.
- L2 (KB worktree step): accepted; item 5 reuses the existing locked worktree, item 2's dry run runs after `uv sync`.
- L3 (explicit delete beside the cascade): rejected. `get_connection` sets `PRAGMA foreign_keys=ON` on every
  connection (`db.py:397`), `embed_truncations` and `embed_refusals` rely on the same cascades, and migrations turn it
  off only around table rebuilds that do not touch folder summaries; an explicit delete is defensive code for a state
  that cannot happen. The reviewer accepted the rejection in round 2.
- L4 (ME doc line outside "fixture only"): no change; already surfaced as item 1 of "For Michaël".

Round 2 (revision 2): LGTM, no new findings.

Round 3 (revision 3, SHA-256 17a10967…, the final plan): LGTM over a two-sentence consistency fix (Outcome and
Testing now say keys get `scrub`, as D4 does). No findings.

Degradation: this reviewer stands in for Codex in the Full gate's Codex slot (Codex quota out until about
2026-11-25, Michaël 2026-11-22: "sonnet subagent medium (especially when plan was drafted by opus planners)").

# Implementation audit (pre-review): #569 explicit embedding endpoint

- Scope: pre-review
- Approved plan: `docs/plans/2026-11-16-569-explicit-embedding-endpoint.md`, SHA-256
  `12997b016ebded1a3eac4956fb6028af1a0687494543097f776c1a5db7c14528` (approved on #577 by dutiona, 2026-11-16T17:53:28Z)
- Implementation head audited: `1cd9eaf8010a3dcdbf4ad7f12b0af55015badfbe`

| Item | Status | Commit(s) | Note |
| --- | --- | --- | --- |
| 0 Plan on the branch | Implemented | `049532a` | |
| 1 Status prints no key | Implemented | `dc434c4` | |
| 2 Dead `embed_provider_config` | Implemented | `6f4a8a0` | |
| 3 One test declaration | Implemented | `fef8db7` | Variation: the constants are `TEST_EMBED_*` (the plan said `EMBED_*`), so they never read as the deprecated `EMBED_PROVIDER`; same pass count before and after (1131 passed, 9 skipped, full suite) |
| 4 Parse the declaration | Implemented | `a0bb318` | |
| 5 Seed a blank DB | Implemented | `5ec98b4` | Variation: a legacy DB with `embed_model` but no `embed_dim` now raises instead of falling back to 1024 (the last default fallback) |
| 6 Bind an existing DB | Implemented | `1fb63ec` | |
| 7 Providers from the declaration | Implemented | `cbb448d` | Also removed the two superseded drift tests in `tests/test_search.py` |
| 8 Remove `configure_embeddings` | Implemented | `acca8ae` | `tests/test_routes_embeddings.py` needed no change (plan listed it) |
| 9 One key source | Implemented | `4ad86f3` | |
| 10 Vision off | Implemented | `eb8246a` | |
| 11 Browser off | Implemented | `a17afed` | |
| 12 LLM off + key allowlist | Implemented | `f51982f` | |
| 13 Startup gate | Implemented | `1f83b04`, `1cd9eaf` | Variation: "ingest without a declaration exits 1" was already green after item 5 (a blank DB refuses); its red came from the disagreeing-DB case. `1cd9eaf` makes the subprocess test's suppression hold under both the locked ruff 0.15 and the host ruff 0.16 the merge gate now uses |
| 14 Identity via the declaration | Implemented | `556d425` | Includes the two round-8 LOWs arbitrated into this item (route handlers catch `KnowledgeBaseError`, also on `backfill_embed_space_tool`; the CLI's redundant `similar` delete removed). Variation: `promote_space` also runs its completeness check before the summary embed (fail fast), and again under the lock |
| 15 Exemption | Implemented | `6b6ce55` | |
| 16 ADR-0018 amendment | Implemented | `8482c35` | |
| 17 Documentation | Implemented | `0027d18` | `docs/getting-started/quickstart.md` has no configuration content, so nothing to change. The check's remaining hits are the README lines documenting that `EMBED_PROVIDER` is refused and one historical docstring in `settings.py` naming the pre-#569 rule |
| 18 Inventory and agent notes | Implemented | `6829b0a` | Variation: the plan's premise that `allow_loopback` is no longer shared was wrong (the embedding path still reads it), so that docstring stays; the `api_key` line was the stale one |
| 19 Gates, PR, review, finish | In progress | | |

Counts: Implemented 19, In progress 1 (operational), Modified 0, Dropped 0, Incomplete 0, Added 0.
Divergence (Modified + Dropped + Added) / 20 = 0%. No successor plan.

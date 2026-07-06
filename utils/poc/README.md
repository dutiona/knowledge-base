# E0 PoC scripts (#495)

Two proof-of-concept probes that must run and **pass** before any E0
substrate work (whitening + CSLS + registry) is committed. Both are
**corpus-gated** per #495's gate box and refuse to run under-gate:

| Script | Question | Gate |
| --- | --- | --- |
| `poc1_anisotropy_audit.py` | Is the space anisotropic, and does mean-center + All-but-the-Top restore isotropy (random-pair cosine → 0)? | corpus ≥ 10K chunks (`--allow-small` = ABT-only sweep, no verdict); recall axis needs the #250 golden set |
| `poc2_csls_hub_histogram.py` | Does CSLS flatten the N_k hub distribution (skewness, never-retrieved share)? | corpus ≥ 10K chunks (`--allow-small` = exploratory only) |

Both are read-only against the DB, seeded for reproducibility, and emit a
JSON report. Verdicts (pass/fail + threshold reasoning) are an owner+Fable
checkpoint — never delegated (Tier-1 table, spike session 2026-07-06).

If the PoCs fail: E0 goes dormant, not deleted (ADR-0017 stays a correct
contract awaiting a bigger corpus) — #495.

Operational caveats: only `float32` spaces are supported (int8 spaces are
refused with a message); the strictly read-only open can fail against a live
WAL DB whose `-wal` needs recovery — run against a checkpointed copy (or the
throwaway campaign DB) when in doubt. PoC-2's `--sample` cap (default 20000)
bounds the O(n²) similarity matrix at ~1.6 GB.

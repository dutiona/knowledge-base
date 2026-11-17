# Final audit (post-review): 2026-11-17-588-ingest-caps

- Approved plan: `docs/plans/2026-11-17-588-ingest-caps.md`, sha256
  `a404bd10683837ab045c0198184ec0aae20073185dc31657fcd5fcdbb7dbf205` (marker on #601, comment 6951466594).
- Audited head: `2655d44` (scope: post-review, after five Full-gate rounds: Codex, agy and a clean-slate Opus lens;
  all three LGTM at `0f00749`; `2655d44` only types a test helper). The plan bytes are unchanged.
- Pre-review audit: `docs/plans/2026-11-17-588-ingest-caps-implementation-audit.md` (head `b2746f8`).

| # | Item | Status | Commits | Note |
|---|---|---|---|---|
| 1 | The fetch limits in `settings.py` | Implemented | `bddad51`, `4165c65` | Review: a value with leading zeros reads as its number (D1's "ASCII decimal integer"); blanks stay stripped like every `KNOWLEDGE_BASE_*` variable |
| 2 | The deadline in the floored transport | Implemented | `1cfa0bd`, `e311a64`, `78328ed`, `49edd99`, `2f9ca2b`, `0f00749` | Review made D2's bound hold for every send of a write (httpcore loops sends under one timeout), closed the TCP stream when the deadline passes before TLS, kept httpx's pool values with one SSL context, declared `httpcore`, and fails closed on a transport without `_pool` or a stream whose socket does not carry its TLS. The loopback body-drip test now drips only the body |
| 3 | `ingest_url` fetches its page through `_fetch` | Implemented | `dd9d544`, `b5b6b77`, `fbe87db`, `5b6309b` | Review: the encoding whitelist compares the whole list (`gzip, identity` refused); the body is one `bytearray` (10 MiB in 2-byte chunks: 455 MiB RSS growth before, 14 MiB after); `_fetch` sends with `follow_redirects=False` and `egress_client`'s dead `follow_redirects` is gone |
| 4 | Inline images through `_fetch` | Implemented | `1b3fbf8`, `a880e0c` | Review: the image body reaches PIL as `bytes`, held once |
| 5 | Documentation | Implemented | `b2746f8`, `9986627`, `49edd99` | The multi-send write residual was documented, then made unreachable and removed |
| + | Decoding and URL robustness | Added | `ac40569`, `6163f2a`, `2fb7d2b`, `6fa7f7d`, `9826cec` | Found in review, on lines this PR rewrote: a non-text or `replace`-refusing charset (base64, rot13, idna, undefined) and an unknown one read as UTF-8; `punycode` reads as UTF-8 (quadratic decode: 0.15 s per 40,000 digits, days at the cap); a malformed URL (non-numeric port, control character, unclosed IPv6 bracket) is a `ValidationError`. The last two predate #588 |

Counts: Implemented 5, Modified 0, Dropped 0, Incomplete 0, Added 1. Divergence: 1/5 = 20% (threshold 30%). The
added item and the notes strengthen the approved decisions without changing their intent (every limit, bound and
residual of D1-D7 stands, minus the write residual the review closed), so no successor plan.

Verification at `2655d44`: the AGENTS.md gate in order, clean tree: `uv lock --check` rc 0, `uv sync` rc 0,
`ruff check` all passed, `ruff format --check` 97 files formatted, `pytest -m "not slow"` 1528 passed / 4 skipped (the
memarchbench stubs) / 10 deselected, `uv sync --all-groups` rc 0, `basedpyright` 0 errors.

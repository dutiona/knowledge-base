# [Antigravity Review] plan review, agy gemini-4-argon-high, conversation afa3282c-52f7-4371-bbed-b2cee8a96e62

## Round 1 (receipt incomplete: stream drain >1 s, cleanup unverified; content complete)

Michaël, the plan is sound in its core security architecture (single `_PinnedTransport` + `egress_client` guard, strict `KNOWLEDGE_BASE_HOST_ADDRESSES` parsing, and full-surface coverage across web, LLM, vision, OmniParser, and CDP), with one **[HIGH]** gap in how OmniParser's `floor` and `allow_loopback` flags are derived and threaded, one **[MEDIUM]** test-migration / `HttpRecorder` gap, and two **[LOW]** items. Confidence: high.

Altitude report SHA-256: 062da951ae911af126207d7ecf80d0ceb4c90d30064d7f613511296bda308367

### Altitude, Duplication, and Structural Completeness

- **Altitude & duplication:** The plan's breadth (522 lines, 15 items across five domain modules) is justified by the owner-approved scope addition (LLM `ollama`, vision, OmniParser, CDP) and ADR-0018 §4's connect-target binding requirement. It contains no fenced code blocks or transcribed implementations, and introduces no duplicate validator (`_PinnedTransport` + `egress_client` for request-time pinning; `validate_base_url` retained for write-time checks, CDP, and the embedder's `exempt_host` check).
- **Structural completeness:** `## Documentation`, `## Testing`, and `## Verification` are all present and complete.

### Findings

[Antigravity Review] [HIGH] **Item 8 & D4 (OmniParser `floor` derivation and unthreaded `allow_loopback`):** Item 8 states that `_check_omniparser_server` and `_run_omniparser_http` take `floor=base_url != _OMNIPARSER_AUTO_URL` and the loopback opt-in, while scoping `src/knowledge_base/web.py` edits to `(three _vision_call sites)`. This has two defects: (1) `_run_omniparser_http` (`src/knowledge_base/vision.py:317`) is called only via `_run_omniparser` (`vision.py:498`), which is called from `_run_omniparser_pipeline` (`vision.py:1638, 1651`) and `_extract_web_figures` (`src/knowledge_base/web.py:806`), neither of which accepts `conn` or `allow_loopback` — so an opted-in loopback `omniparser_server_url` (e.g. `http://localhost:7862` or `http://127.0.0.1:7863` with `allow_loopback_base_url=true`) will pass `configure_omniparser` and `_resolve_omniparser_server_url`'s health check (which has `conn`), then fail with `ValidationError` on every `/parse` call in `_run_omniparser_http` and silently fall back to the subprocess; (2) computing `floor=base_url != _OMNIPARSER_AUTO_URL` by URL string equality instead of provenance violates D4 ("Trust follows where a URL came from"): a pre-existing or previously stored `omniparser_server_url` row equal to `"http://127.0.0.1:7862"` (or `"http://127.0.0.1:7862/"` once normalized) gets `floor=False` even when `allow_loopback_base_url` is `false`. Also, `configure_omniparser` (Item 8) does not state that `server_url` is normalized (`rstrip("/")`) before validation/storage, and `vision.py` has three `f"http://127.0.0.1:{...}"` sites (lines 390, 2093, 2149), not two. Thread `floor` (true iff `configured_url is not None`) and `allow_loopback=loopback_opt_in(conn)` from `estimate_figures_time`, `extract_figures`, `_resolve_omniparser_server_url`, `_run_omniparser_pipeline`, `_run_omniparser`, and `web.py:805-806`, and add a test for an opted-in loopback `omniparser_server_url` reaching `/parse`.

[Antigravity Review] [MEDIUM] **Item 3, Item 4, Item 5 & D5 (`HttpRecorder.hosts()` under pinning and `other.com` in `tests/test_ingest.py`):** In `tests/conftest.py:107-108`, `HttpRecorder.hosts()` currently returns `[request.url.host for request in self]`. Item 3 updates `HttpRecorder` to dispatch `handlers` by the logical host from the `Host` header and adds `targets()` (`request.url.host`), but does not state that `hosts()` also switches to the logical host (with `:port` and IPv6 brackets stripped, matching handler keys like `"page.example.test"` on port 8443 in Item 3 and `"ollama.example.test"` on port 11434 in Item 6). Without updating `hosts()`, `hosts()` duplicates `targets()` and the two `#569` tests in `tests/test_ingest.py:4289, 4314` (`assert http_requests.hosts() == ["page.example.test"]`) will fail in Item 5 once `ingest_url` pins `request.url.host` to the resolved IP address. In addition, D5 (line 156) notes a module fixture for `example.com` in `tests/test_ingest.py`, but `test_extract_html_images_cross_page_content_hash` (`tests/test_ingest.py:2485`) also fetches `https://other.com/fig.png`; once its `is_private_ip` patch is dropped in Item 4, `other.com` must also be covered by `fake_dns` (or renamed to `*.example.test`).

[Antigravity Review] [LOW] **D2 & Item 3 (`_PinnedTransport.close()` delegation):** D2 specifies that `_PinnedTransport(httpx.BaseTransport)` wraps an inner `httpx.HTTPTransport` rather than subclassing it. I think `httpx.BaseTransport.close()` is a no-op in httpx 0.28.1 while `httpx.Client.close()` (and `with egress_client(...) as client:`) calls `self._transport.close()`; ensure `_PinnedTransport.close()` delegates to the inner `HTTPTransport.close()` so sockets in the inner pool are closed deterministically without `ResourceWarning` on GC.

[Antigravity Review] [LOW] **Verification (line 517-518, startup refusal output prefix):** Verification states that `KNOWLEDGE_BASE_HOST_ADDRESSES=198.51.100.7/24 uv run knowledge-base </dev/null; echo $?` "prints a line starting with the variable, then `78`." While `ValidationError` starts with `KNOWLEDGE_BASE_HOST_ADDRESSES`, `src/knowledge_base/server.py:48` prints `f"knowledge-base: {exc}"` to `stderr`, so the printed line starts with `knowledge-base: KNOWLEDGE_BASE_HOST_ADDRESSES`.

REVIEW COMPLETE

## Round 2

Michaël, the Round 2 delta cleanly resolves all Round 1 findings and introduces no new issues. High confidence.

Altitude report SHA-256: 6fdd2696a7538aed9eaa639169ba16d75b01146ffd4bd3dd3011d2d92523090a

[Antigravity Review] LGTM
REVIEW COMPLETE

## Round 3

Michaël, the Round 3 delta is sound and ready to proceed. High confidence.

Altitude report SHA-256: 682395fe9b427640a2b981a546630d0517f81fa23cc67609618d85521274f95e

[Antigravity Review] LGTM
REVIEW COMPLETE

## Round 4

Michaël, the Round 4 delta is clean and ready to proceed. High confidence.

Altitude report SHA-256: 54baa26a1776daf85087bd84e0f1a19ac98e5008f6c5a0afc6d31782a01bee43

[Antigravity Review] LGTM
REVIEW COMPLETE

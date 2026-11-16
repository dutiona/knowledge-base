# Clean-slate subagent review (Claude Opus, general-purpose, round 1)

Altitude report SHA-256: 062da951ae911af126207d7ecf80d0ceb4c90d30064d7f613511296bda308367

**Verdict: LGTM with changes, no blockers.** I'm highly confident in the mechanism, because I checked every load-bearing httpx/httpcore claim against the installed sources. Two MEDIUM contract gaps should be fixed in the plan before implementation starts: the `egress_client` kwargs and the SNI name form.

## Altitude and duplication (assessed first)

The altitude report's digest matches. The plan's own SHA-256 is `38c094b3…0f8180`, which matches the digest the report records. Size is 522 lines / 37 881 bytes, also as recorded.

I agree with **proceed**. Breadth comes from the owner-approved scope addition and from ADR-0018 §4's "actual connect target" contract. There is no transcribed product code. There is one floor rule: `_refused` → `_accepted_addresses`. It is reused by `is_private_ip`, `validate_base_url`, `_validate_image_url` and the transport, so there is no second validator.

The D4 table is restated across Items, Documentation and the PR body. That is acceptable for a security patch whose reviewers need the trust map in one place.

No override is needed. None is claimed, and the report lists no required fallback or provenance inputs.

## Mechanism claims verified against httpx 0.28.1 / httpcore 1.0.9

- **`sni_hostname`**: `httpcore/_sync/connection.py:107,151` uses it as `server_hostname` for start_tls. So SNI and certificate hostname checking both follow it.
- **`Host` header**: a fresh `httpx.Request` with a `Host` header keeps it (`_models.py:450`, `has_host`). I probed this: the IP URL plus `Host: page.example.test:8443` is preserved.
- **Proxies**: `allow_env_proxies = trust_env and transport is None` (`_client.py:685`) confirms D2/OC2 for environment proxies. But an *explicit* `proxy=` still builds an `all://` mount (`_get_proxy_map`, line 249) that bypasses the transport. See finding 1.
- **Silently ignored kwargs**: `_init_transport` returns the given transport unchanged, so a Client's `limits`, `verify`, `cert`, `http2` and `trust_env` are dropped when `transport=` is passed.
- **Redirects**: every hop goes through `_send_single_request` → `transport.handle_request`. After that, `response.request = request` (the client's original, line 1018), so `response.url`, cookies and `Location` resolution stay on the name. `_redirect_headers` strips `Authorization` across origins and rewrites `Host`.
- **Non-http redirects**: a 302 to `ftp://` reaches the transport. httpx does not reject it first, so D2.1's scheme check is needed.
- **No network backend**: `HTTPTransport.__init__` accepts none, so the rejected alternative is correctly rejected.
- **Connection reuse**: the httpcore pool is keyed by origin (the IP). `max_keepalive_connections=0` closes every idle connection before queued requests are assigned (`connection_pool.py:284-299`). Without it, reuse would send name B over a TLS session that was only verified for name A. So D2.6 is a correctness requirement, not just hygiene.
- **IPv6 brackets**: `URL.copy_with(host="2001:db8::10")` adds the brackets itself.
- **IDNA**: `raw_host` is the IDNA2008 form (`b'xn--fa-hia.example.test'`).
- **`ipaddress` on Python 3.12.14**:
  - `::ffff:127.0.0.1` and `100.64.0.7` are non-global.
  - `64:ff9b::7f00:1` and `::7f00:1` are global, which matches residual D6.3.
  - `2002::/16` (6to4) is already non-global.

## Findings

**1. [MEDIUM] D2 / Item 7: `egress_client(**kwargs)` contradicts D2.6 and lets kwargs bypass the guard.**
- Item 7 passes `limits=httpx.Limits(max_connections=…, max_keepalive_connections=effective_workers)` for the map-phase pool, and httpx silently drops it under `floor=True`.
- The natural "fix" is to forward `limits` to the inner transport. That turns keepalive back on and breaks D2.6. The socket test only weakens to the *default* limits, so it would not catch this.
- Separately, `proxy=` or `mounts=` passed through `**kwargs` creates mounts that skip `_PinnedTransport` entirely. That makes the one guard opt-out by accident.
- **Fix:** with `floor=True`, `egress_client` accepts only client-level kwargs (`timeout`, `follow_redirects`, `headers`). It raises `TypeError` on `transport`, `mounts`, `proxy`, `verify`, `cert`, `http2` or `limits`. Item 7's pool becomes `egress_client(floor=…, allow_loopback=…)` with no `limits`; the `ThreadPoolExecutor` already bounds concurrency. Add a unit test that `egress_client(floor=True, proxy=…)` raises.

**2. [MEDIUM] D2.4 / Item 3: the SNI value must be `request.url.raw_host.decode("ascii")`, not `request.url.host`.**
Python's `ssl` encodes a `str` `server_hostname` with the IDNA2003 codec. I checked: `ctx._encode_hostname('faß.example.test')` gives `'fass.example.test'`. That means a wrong SNI name and a certificate checked against the wrong name. The same ASCII string must feed `getaddrinfo`; also, `fake_dns` is keyed by `str` and would fail closed on `bytes`. Extend the IDNA test to assert `sni_hostname == "xn--fa-hia.example.test"`.

**3. [LOW] Item 3 TLS socket test: the certificate and the client's trust are unspecified.**
- The test can't pass `verify=` through `egress_client`, because finding 1 shows it is dropped.
- `cryptography` is only a transitive dependency (via authlib), and the Verification gate says no dependency is added.
- The `sni_callback` fires during the ClientHello even when the server has no certificate. So the test can load no certificate, assert the recorded SNI, and expect `httpx.ConnectError` with a single address.
- State that approach in the plan, or name the certificate source.

**4. [LOW] Item 7: `_test_llm_connectivity` has no floor input.**
"Probes through the same client" does not say where `floor` comes from. Today the signature is `(provider, base_url, api_key, *, allow_loopback)`, and `configure_llm` calls it from `cfg`. Add `floor=cfg["floor"]` explicitly. Otherwise a probe of an auto-detected Ollama gets floored, or a stored URL's probe does not.

**5. [LOW] Item 8 contradicts D4: OmniParser trust is decided by value, not source.**
`floor=base_url != _OMNIPARSER_AUTO_URL` leaves a stored row equal to `http://127.0.0.1:7862` unfloored, including rows written before this change. The exposure is negligible, but it breaks D4's "trust follows where a URL came from". Use `floor = configured URL row is present`, the same rule as `_get_llm_config`'s `base_url_row is not None`.

**6. [LOW] Items 6 and 7: the migration check misses 18 `@patch("knowledge_base.utils.is_private_ip", …)` lines in `tests/test_llm.py`.**
After Item 7, request-time pinning no longer calls `is_private_ip`. Those patches go inert and the tests resolve hosts such as `x.example.com` over real DNS. The hermetic run would catch this, but only at the end. Add `knowledge_base\.utils\.is_private_ip` in `test_llm.py` to Item 6's `rg` check. `test_embeddings.py` keeps its 6 patches legitimately.

**7. [LOW] Item 2/3 ordering: `public_net` is created in the wrong item.**
Item 2's red tests use `public_net`, but Item 3's Change list is what adds it. Before Item 2's change, patching `knowledge_base.utils._is_public` raises `AttributeError`, so the red state is an error, not "fails on the assertions". Move the fixture into Item 2.

**8. [LOW] Item 3: `HttpRecorder` semantics are left implicit.**
- The `Host` header carries the port (`page.example.test:8443`); dispatch must strip it.
- Say what `hosts()` returns after the change. `tests/test_ingest.py:4289,4314` assert `hosts() == ["page.example.test"]` and break after Item 5 unless `hosts()` becomes the logical host.
- As written, `targets()` is identical to today's `hosts()`.

**9. [LOW] Item 0: the `git status --short` empty check will fail.**
`docs/plans/` also holds the untracked `-altitude.md` and `-advisor.md`. Say whether they are committed alongside the plan or deleted with `-drafts/`.

**10. [LOW] Item 2: the single error message covers DNS failure too.**
A failed lookup (a transient resolver error on an LLM or vision host) is reported as "private, reserved or this host's own address". It is fail-closed either way. A separate `cannot resolve {host}` message costs one branch and saves misdiagnosis. Today's `ingest_url` has the same conflation, so this is your call, Michaël, not a regression.

**11. [LOW] D6.3: the residual list is incomplete.**
IPv4-translated `::ffff:0:a.b.c.d` (`::ffff:0:0:0/96`) belongs with NAT64 and IPv4-compatible forms as a stated residual, so the list is complete. It needs no code.

**12. [LOW] Item 8: the `_vision_call` signature change breaks existing test fakes.**
The four `fake_vision_call(image_b64, prompt, *, base_url, model)` fakes in `tests/test_multi_figure.py` (around 297–418) break on the new required `floor` and `allow_loopback` keywords. The file is in Item 8's list, but the plan should name these fakes so the red/green attribution stays clean.

## Structural completeness

Documentation, Testing and Verification sections are all present and specific. Verification includes the hermetic `unshare -rn` run, the startup exit-78 check, and grep proofs that no direct `httpx.get`/`post`/`stream` remain in `web.py`, `llm.py` or `vision.py`. I confirmed the doc claims behind Item 11 against the files:
- `env:ANTHROPIC_API_KEY` in `docs/usage/structured-extraction.md`:183;
- `192.168.1.41` in `docs/reference/mcp-tools.md`:607;
- the figure-extraction text says "inline extraction is skipped" and "`source_url` (post-redirect)", while the code passes `source_url=url`.

## Feasibility

Items 1→14 run in a workable order. `loopback_opt_in` arrives in Item 7, before Items 8 and 9 use it. Both test-migration items run green on the unchanged source because `http_requests` patches below every call form. The only ordering defect is finding 7.

## Resolution

Round 1, applied to the plan (new plan SHA-256 recorded in the refreshed altitude report):

- [MEDIUM] 1, `egress_client(**kwargs)`: fixed. With `floor=True` it takes only `timeout`, `follow_redirects` and
  `headers`, with no `**kwargs`, and a test asserts `TypeError` for `proxy`, `mounts`, `transport`, `limits` and
  `verify`. The map-phase pool drops `limits`, since the executor bounds concurrency (D2, items 3 and 7).
- [MEDIUM] 2, the SNI form: fixed. `name = raw_host.decode("ascii")` feeds both the lookup and `sni_hostname`, and
  the IDNA test asserts the `xn--` SNI (D2.2, item 3).
- [LOW] 3, TLS test certificates: `trustme` is now a dev dependency, with its CA trusted via `SSL_CERT_FILE`. The
  tests add a positive verification case and a mismatched-name negative case (item 3, Testing, Verification).
- [LOW] 4, the connectivity probe's floor: `_test_llm_connectivity` gains a required `floor`, and `configure_llm`
  passes `cfg["floor"]` (item 7).
- [LOW] 5, OmniParser trust by value: fixed by provenance. An endpoint mapping is threaded from the read down to
  the request, and a test covers a stored row equal to the auto URL (D4, item 8).
- [LOW] 6, the 18 `utils.is_private_ip` patches in `test_llm.py`: added to D5 and to item 6's `rg` check.
- [LOW] 7, `public_net` ordering: the fixture moves to item 2.
- [LOW] 8, `HttpRecorder` semantics: specified. Dispatch and `hosts()` use the logical host, with the port and
  brackets stripped; `targets()` returns the connect target (item 3).
- [LOW] 9, the Item 0 status check: item 0 commits the plan with its review evidence, and the drafts are deleted.
- [LOW] 10, the DNS-failure message: a separate `cannot resolve {host}` message is added, still fail-closed (item 2).
- [LOW] 11, the IPv4-translated form: added to residual D6.3.
- [LOW] 12, the `test_multi_figure` fakes: named in item 8.

# Round 2 delta review (same subagent, resumed; plan f4e51f6e…)

Altitude report SHA-256: 682395fe9b427640a2b981a546630d0517f81fa23cc67609618d85521274f95e

**Verdict: LGTM. No blocker, high or medium findings; three LOW findings remain.** All 12 round-1 findings are resolved, and the two MEDIUMs are fixed in full. The LOWs below can be folded in without another review round. I'm highly confident: I re-checked the new mechanism claims against the installed httpx.

## Altitude (assessed first)

- The refreshed report's first line is `Plan SHA-256: f4e51f6e8c9e066dc64ea6c1d756131cdcc8c935fdff495a823fe440025bd839`. That matches `sha256sum` of the current plan.
- The "Round 3 refresh" size (597 lines, 44 719 bytes) matches `wc`.
- Nit: the top "Size" bullet still gives the round-2 numbers (591 lines, 44 254 bytes). The refresh sections correct it, so nothing is lost.
- The growth from 522 to 597 lines traces entirely to round-1 and round-2 findings. No product code is transcribed and no second validator is added. Provenance threading reuses one endpoint mapping instead of a new owner.
- I agree with **proceed**. No override is claimed or needed.

## Round-1 fixes

| # | Fix | Verdict |
| --- | --- | --- |
| 1 | `egress_client` takes only `timeout`, `follow_redirects` and `headers`; no `**kwargs`; a `TypeError` test; the map pool drops `limits` | Right and complete. `TypeError` comes free from the closed signature. The executor bounds concurrency, and with keep-alive off each in-flight request holds one connection. D2.7, the `close()` delegation, is a good addition. |
| 2 | `name = raw_host.decode("ascii")` feeds both the lookup and SNI; the test asserts the `xn--` SNI | Right. |
| 3 | `trustme`, a positive TLS test and a mismatched-name negative | Right. httpx 0.28 `_config.py:34` reads `SSL_CERT_FILE` when `verify=True`, and the inner `HTTPTransport` keeps `trust_env=True`. The env var must be set before `egress_client` is built, which `monkeypatch.setenv` in the test does naturally. The round-3 weakening for the mismatch test (`check_hostname = False`) is correctly reasoned. Dropping SNI would make the client check the IP instead, and the test would stay green. The `uv lock --check` wording was updated to match the new dependency. |
| 4 | `_test_llm_connectivity(..., *, floor)`, fed `cfg["floor"]` | Right. |
| 5 | OmniParser endpoints carry provenance; a row equal to the auto URL is floored | Right in principle. One call-site gap, LOW-1 below. |
| 6 | The 18 `utils.is_private_ip` patches go into D5 and Item 6's `rg` | Right. |
| 7 | `public_net` created in Item 2 | Right. |
| 8 | Recorder dispatch by authority; `hosts()` returns logical hosts; `targets()` the connect target | Right. The round-3 `httpx.URL(f"http://{host_header}").host` parse plus its self-test handles the port and IPv6 brackets. One edge case, LOW-3. |
| 9 | Item 0 commits the plan with its review files; `/finish-pr` removes them | Right. `git status --short` will be empty. |
| 10 | A distinct `cannot resolve {host}` message | Right. It creates one new test hazard, LOW-2. |
| 11 | IPv4-translated form added to residual D6.3 | Right. |
| 12 | The `test_multi_figure` fakes named in Item 8 | Right. |

**OC8 (new):** a CDP endpoint must be an IP literal, or `localhost` under the opt-in. It is sound, because Playwright's own lookup can't be bound. It is correctly raised as an owner call with its consequence: a hosted browser service reached by name is refused.

One thing to check when Item 10 is written: `docs/reference/mcp-tools.md:668` shows `"endpoint": "ws://localhost:3000"`. That example now needs the opt-in, and the IP-literal rule should appear in the `configure_browser_tool` text, not only in the ADR amendment.

## Findings introduced or left by the delta

**LOW-1. Item 8 does not cover two OmniParser call sites.**
The ETA paths in `vision.py` (around lines 2091–2094 and 2147–2150) read `_get_omniparser_server_url(conn)` directly and call `_check_omniparser_server(check_url, require_models=True)`. The plan replaces their f-strings with `_OMNIPARSER_AUTO_URL`. But the mapping is threaded only through `_resolve_omniparser_server_url`, `_run_omniparser_pipeline`, `_run_omniparser` and `web._extract_web_figures`. Since `_check_omniparser_server` now needs `floor`, these two sites must build the mapping too. Otherwise they infer trust from the value again, which is exactly what fix 5 removed.

Fix: add one helper, for example `_omniparser_endpoint(conn) -> mapping`, returning the stored row (floored) or the auto URL (unfloored). Use it in both ETA sites and inside `_resolve_omniparser_server_url`. `_ensure_omniparser_server`'s two health checks (around 393 and 400) pass the auto mapping, unfloored.

**LOW-2. The new message breaks one Item 5 test case.**
In Item 5, `test_a_page_redirect_into_a_refused_address_is_never_sent[localhost]` expects `match="private"`. The plan gives `localhost` no `fake_dns` answer. Under the fail-closed `fake_dns`, it now raises `cannot resolve localhost` instead, so the case fails for the wrong reason. Give it `fake_dns["localhost"] = ["127.0.0.1", "::1"]` so it tests the floor, not the resolver.

The fix is also only half-applied. `validate_base_url` still turns `is_private_ip`'s bool into the "private" message. So a write-time resolver failure is still reported as a private address, while a request-time failure says `cannot resolve`. That is acceptable, since both fail closed and `test_embeddings` patches `is_private_ip`, but the docs should not promise the distinct message for writes.

**LOW-3. Recorder dispatch drops IDNA names to Unicode.**
`httpx.URL(...).host` decodes an IDNA `Host` back to Unicode: `xn--fa-hia.example.test` becomes `faß.example.test`. Handlers for IDNA names must therefore be keyed by the Unicode form. Either key them that way, or use `.raw_host.decode("ascii")` in dispatch, and say which in the conftest self-test. Only the Item 3 IDNA test is affected.

## Resolution (round 2 delta)

- [LOW-1] OmniParser ETA call sites: fixed with one `_omniparser_endpoint(conn)` helper, used by both ETA sites,
  by `_resolve_omniparser_server_url` and, as the auto mapping, by `_ensure_omniparser_server`'s health checks
  (item 8).
- [LOW-2] The `localhost` case: it now gets `fake_dns` answers, so it tests the floor (item 5). The `cannot
  resolve` message is documented as request-time only; write-time `validate_base_url` keeps the floor's message
  (Documentation).
- [LOW-3] Recorder IDNA: dispatch now uses `raw_host.decode("ascii")`, so handlers are keyed by the `xn--` form
  (item 3).
- Doc note: the `configure_browser_tool` text and the `ws://localhost:3000` example in `mcp-tools.md` (~668) now
  carry the opt-in and the IP-literal rule (Documentation).
- Altitude nit (stale top Size bullet): fixed in the round-4 refresh.

# Round 4 delta review (same subagent, resumed; plan 7f2a54ee…)

Altitude report SHA-256: 54baa26a1776daf85087bd84e0f1a19ac98e5008f6c5a0afc6d31782a01bee43

**LGTM.** The round-4 delta resolves all three of my round-2 LOWs and the doc note correctly, and adds nothing new above a nit. I'm highly confident; the delta is 4 hunks and I checked each against the code and httpx behaviour I had already verified.

## Altitude

- The report's first line is `Plan SHA-256: 7f2a54eeb798c4bdd06d5a3fbd52817e378af744bf0c84d5e678089ee508d8f4`, which matches `sha256sum` of the current plan.
- The size bullet now reads 607 lines / 45 624 bytes (round 4), matching `wc`. It lists the earlier rounds too, so my round-2 nit about the stale size is fixed.
- The +10 lines trace only to the round-2 findings and one doc bullet. No new code, validator or owner was added.
- **Proceed** is still right. No override is claimed or needed.

## Round-2 LOWs

- **LOW-1, OmniParser helper: resolved.**
  - One helper, `_omniparser_endpoint(conn)`, returns the stored row as a floored mapping with the opt-in, or the auto endpoint with `floor: False`.
  - Both ETA sites (~2091, ~2147) and `_resolve_omniparser_server_url` use it, and `_ensure_omniparser_server`'s two health checks pass the auto mapping.
  - That covers every `_check_omniparser_server` and `_run_omniparser_http` caller I found in `vision.py` and `web.py`, so trust is no longer inferred from the URL's value anywhere.
- **LOW-2, `localhost` in the Item 5 redirect test: resolved.** `fake_dns["localhost"] = ["127.0.0.1", "::1"]` makes the case reach the floor. `ingest_url` never gets the loopback opt-in, so it is refused with the "private" message as the test expects.
- **LOW-3, IDNA recorder key: resolved.** `httpx.URL(f"http://{host_header}").raw_host.decode("ascii")` gives the `xn--` form, drops the port, and unbrackets IPv6 (`raw_host` for `[::1]` is `b'::1'`). My earlier probe confirmed that `.host` decodes back to Unicode, so the rationale is right.
- **Doc note: resolved.**
  - `docs/reference/mcp-tools.md` now gets the IP-literal rule (OC8) and the opt-in note on the `ws://localhost:3000` example.
  - It promises the distinct `cannot resolve` message only for request-time lookups, which matches what `validate_base_url` actually does.

## Nits (non-blocking)

- **Recorder fallback form.** The fallback `request.url.host` is the Unicode form, not the ASCII key. httpx always sets `Host`, so it is practically never reached. Making it `request.url.raw_host.decode("ascii")` keeps one key form. Also consider adding an IDNA name to the conftest self-test, next to `page.example.test:8443`, `[2001:db8::10]:8443` and the portless name.
- **Line length.** The new recorder line in Item 3 is longer than the plan's roughly 120-column wrap. It is cosmetic.

## Resolution (round 4)

- Nits only (cosmetic or low; exit condition 2), arbitrated by the controller with no further plan revision, so the reviewed bytes stand. Both are adopted at implementation: in item 3 the recorder fallback uses `request.url.raw_host.decode("ascii")`, and the conftest self-test adds an IDNA name. The line length is cosmetic and is not changed.

# fix(web): validate and pin every redirect hop, and refuse dutiona's own addresses (#572)

## Outcome

KB opens no connection on a tool's behalf to an address the SSRF floor refuses, whether that address comes from
the URL, a redirect hop or a second DNS answer. The floor refuses any address that is not `is_global`, and also the
host's own addresses listed in `KNOWLEDGE_BASE_HOST_ADDRESSES`. An IPv4-mapped answer is judged as its IPv4 address.

One guard in `utils.py` binds the actual connect target (ADR-0018 §4). Each floored request resolves its host
once and is refused unless every answer passes the floor. It is then sent to an accepted answer, with the hostname
kept for `Host`, SNI and certificate verification.

- `ingest_url` and its image downloads follow redirects through this guard.
- The LLM (Ollama included), vision and OmniParser URLs a `configure_*` tool stored are floored when written and
  pinned on every request.
- The CDP endpoint is floored when written and when used.
- Endpoints the operator's environment yields stay trusted: the declared embedder, `OLLAMA_HOST` or the
  auto-detected Ollama, and KB's auto-started OmniParser.
- A malformed host-address list stops the server at start.

With vision, the browser and LLM off (the default, and the deployment), the visible changes are the pinned web
fetch and the new variable.

## Constraints

- Title: fix(web): validate and pin every redirect hop, and refuse dutiona's own addresses
- Outcome: Once KB is networked, URL ingest can't be turned into a relay into dutiona or the tailnet, by a redirect, by DNS rebinding, or by a URL on dutiona's own public address.
- Every redirect hop is validated against the SSRF floor, or redirects are not followed. Today httpx follows them and only the final host is checked (`web.py:1088`).
- Each hop connects to the address it validated, so a second DNS answer can't swap it.
- The host's own addresses (dutiona's public IPv4 and IPv6, from an environment list that dutiona/infra#81's quadlet sets) are refused, because they are global yet routed to dutiona's own services.
- The image-fetch path (`web.py:409`) gets the same rules, so it no longer depends on vision being off.
- Tests: a redirect from a public URL to `100.64.0.0/10`, to loopback, to `host.containers.internal` or to a listed host address is refused;
- Tests: a host whose second resolution returns a tailnet address is refused.
- The PR references KB #232 (the `[super-qa]` umbrella that carries this redirect finding) without closing it.
- Likely files: `src/knowledge_base/web.py`, `src/knowledge_base/utils.py`, `tests/test_web_redirects.py` (new).
- Depends on #569 (merged). PRs reference the umbrella as `Refs dutiona/infra#37`, never a bare `#37`.
- Contracts win over restatements: a merged ADR-0020/ADR-0015 overrides any restatement here.
- Scope addition (owner-approved issue comment, 2026-11-16): "This issue also takes the remaining SSRF floor gaps: the `ollama` LLM base URL is not checked against the floor when `KNOWLEDGE_BASE_LLM=on`; `configure_vision`, `configure_omniparser` and `configure_browser` check only the URL scheme. All are inactive while their switches are off (the default after #569), and each should go through the same floor as tool-supplied URLs." Owner decision: "I agree with your recommendations (1, 2 & 3)."
- #569 D5, settled: `validate_base_url(url, *, allow_loopback=False, exempt_host=None)`. Only `OpenAICompatProvider.embed` passes `exempt_host`; `llm.py`, `web.py` and every `configure_*` path never do. The docs say: "A URL a tool supplies (`ingest_url`, image fetches, `configure_llm_tool`) always keeps the floor, the declared host included."
- #569 D6, settled: vision, the browser and LLM are off unless `KNOWLEDGE_BASE_{VISION,BROWSER,LLM}=on`.
- ADR-0018 §4 (contract): one guard on every user-supplied base URL. Loopback passes only through the opt-in `allow_loopback_base_url`, and only for a loopback literal or the exact label `localhost`. "The guard binds the _actual connect target_ of each request, before and after any redirect — never just the configured string."
- KB-6 (this issue) is the in-process layer. INF-11's nftables egress chain is the second layer and is not this issue's work.
- Public repository: `*.example.test` hosts and documentation or synthetic addresses only.
- Merge gate: `uv lock --check`, `ruff check src/ tests/`, `ruff format --check src/ tests/`, `uv run pytest tests/ -q -m "not slow"`, `uv sync --all-groups` then `uv run basedpyright src/ tests/`.
- Commits: Conventional Commits, imperative, the body explains why, no Co-Authored-By line on branch commits. TDD: the failing test comes first.

## Implementation sequence

### Decisions

**D1. Redirects are followed, and every hop is floored before it is sent.**
- `ingest_url` and the image downloads keep `follow_redirects=True`, with httpx's limit of 20 hops. Every hop passes
  through the guarding transport, so httpx keeps its redirect rules: method rewrite, `Authorization` stripped
  across origins, relative `Location`.
- Refusing redirects outright would break DOI resolvers, http→https upgrades and CDN image redirects.
- LLM, vision, OmniParser and embedder calls keep `follow_redirects=False`.
- Consequence: a hop into a refused address fails `ingest_url` with the floor's message naming that hop's host, and
  nothing is sent there. Today the request reaches the target, and only its processing is refused. A refused image
  hop skips that image and logs a warning.

**D2. Pinning rewrites the request's address and keeps its name.** `utils._PinnedTransport(httpx.BaseTransport)`
wraps an inner `httpx.HTTPTransport(limits=httpx.Limits(max_keepalive_connections=0))`. It wraps rather than
subclasses, so the `http_requests` recorder (which patches `HTTPTransport.handle_request`) sees the pinned request.
For each request it:
1. refuses a scheme other than `http`/`https`;
2. takes `name = request.url.raw_host.decode("ascii")`, the IDNA 2008 form httpx connects to, and uses it for both
   the lookup and the SNI. A `str` holding Unicode would be IDNA 2003-encoded by `ssl`, so `faß` would become
   `fass`. It resolves `name` once, with `getaddrinfo(name, None, type=SOCK_STREAM)`. Answers are deduplicated in
   resolver order, and a literal answers for itself;
3. refuses the request unless every answer passes the floor. A mixed public and private answer is refused as a
   whole, as today. No partial filtering;
4. sends a fresh `httpx.Request`. Its URL is `request.url.copy_with(host=<address>)`, with IPv6 bracketed and the
   port kept. Its headers are the original ones, so `Host` keeps the name. Its extensions are the original ones plus
   `sni_hostname=<name>`; httpcore uses that name for SNI and for certificate hostname verification. The client's
   own request is never mutated, so `response.url`, `history`, cookies and `Location` resolution stay on the name;
5. moves to the next accepted address only on `httpx.ConnectError` or `httpx.ConnectTimeout` (nothing has been
   sent), and re-raises the last one;
6. never reuses a kept-alive connection. The inner pool is keyed by address, so reuse could carry name A's TLS
   session to name B on the same address. This is a correctness requirement (httpcore keys its pool by origin);
7. delegates `close()` to the inner transport, so `with egress_client(...)` closes its sockets.

`egress_client(*, floor: bool, allow_loopback: bool = False, timeout=..., follow_redirects: bool = False,
headers=None) -> httpx.Client`. `floor` has no default, so every call site states its trust.
- `floor=True` builds a client on the transport. It takes only those client-level arguments: no `**kwargs`. A
  client given `transport=` silently drops `limits`, `verify`, `cert`, `http2` and `trust_env`, and
  `proxy`/`mounts` would add mounts that bypass the guard. No caller can opt out of the guard by an argument.
  Concurrency is bounded by the caller (extraction's `ThreadPoolExecutor`), never by pool limits.
- `floor=False` returns a plain `httpx.Client` with the same arguments, for an operator endpoint.
- Under `allow_loopback`, a loopback literal or the exact label `localhost` passes unpinned (ADR-0018 §4). A name
  that merely resolves to loopback stays refused.

Rejected: swapping httpcore's network backend. httpx 0.28's `HTTPTransport` takes no backend, so it would mean
replacing the private `_pool`.

Consequence: TLS keeps verifying the site's name. A floored client does one TCP and TLS handshake per request,
which is negligible next to page fetches and 15–120 s model calls. `HTTP(S)_PROXY`/`ALL_PROXY` no longer apply to
floored requests, because a client given a transport builds no proxy mounts (OC2).

**D3. The host-address list.**
- Name: `KNOWLEDGE_BASE_HOST_ADDRESSES`, set by dutiona/infra#81's quadlet (OC1).
- Grammar: comma-separated IPv4 or IPv6 addresses or CIDR networks, with whitespace around entries ignored. Unset
  or empty means no list.
- Parsing is strict (`ipaddress.ip_network(strict=True)`). It refuses an empty entry, a name, host bits set, and an
  entry inside `::ffff:0:0/96`: the floor judges mapped answers as IPv4, so that entry would never match.
- `settings.host_networks()` parses it at call time, like every #569 reader. `check_environment()` calls it, so a
  malformed value exits 78 before serving, with a message that starts with the variable's name.
- `host.containers.internal` is covered by its address, not its name. Depending on the runtime, it resolves to
  link-local (pasta), `10.0.2.2` (slirp4netns) or the host's own address. The floor refuses the first two and the
  list refuses the third.
- Consequence: a typo stops the unit instead of protecting nothing. Unset changes nothing locally. Listing the IPv6
  /64 covers SLAAC and privacy addresses.

**D4. Trust follows where a URL came from.** A URL a tool supplied, directly or through a `configure_*` row, keeps
the floor. An endpoint the operator's environment yields does not.

| Call site | Source | Floor | Pinned | Loopback opt-in |
| --- | --- | --- | --- | --- |
| `ingest_url`, every hop | tool argument | every request | yes | never |
| image downloads, every hop | page HTML | `_validate_image_url` pre-filter plus every request | yes | never |
| LLM, any family, with an `llm_base_url` row | `configure_llm` | write and every request (new for `ollama`) | yes (new) | yes |
| LLM `ollama` without a row | `OLLAMA_HOST`, WSL gateway, `localhost` | no | no | n/a |
| vision with a `vision_base_url` row | `configure_vision` | write and every request (new) | yes (new) | yes |
| vision without a row | `_get_ollama_url()` | no | no | n/a |
| OmniParser `omniparser_server_url` row, whatever its value | `configure_omniparser` | write and every request (new) | yes (new) | yes |
| OmniParser auto-start `http://127.0.0.1:7862` | KB itself | no | no | n/a |
| CDP `browser_endpoint` | `configure_browser` | write and use (new): an IP literal, or `localhost` under the opt-in; a name is refused (OC8) | no lookup to swap | yes |
| declared embedder | `KNOWLEDGE_BASE_EMBED_URL` | exempt by exact host (D5, unchanged) | no | unchanged |

Rows stored before this change are not migrated. Each is refused where it is used:
- an LLM or vision row when its request is built;
- an OmniParser row at its health check, which returns False, so figure extraction falls back to the subprocess;
- a CDP row in `_get_browser_config`, which returns None with a warning, so there is no browser fallback.

Trust is carried as provenance, never inferred from a URL's value. A row's endpoint travels as one mapping,
`{"base_url", "floor", "allow_loopback"}`, from the function that read it (which holds `conn`) down to the request.
`floor` is true exactly when the URL came from a row. So a stored `http://127.0.0.1:7862` row is floored even
though it equals the auto-start URL.

The `ValidationError` raised inside the transport is caught wherever today's code catches only httpx errors (the
OmniParser helpers; the image loop already catches `Exception`).

Remedies, documented:
- `configure_llm_tool(provider="ollama")` without `base_url` (auto-detection);
- `configure_vision_tool(base_url="")`, which now clears the row (OC4);
- `configure_omniparser_tool(server_url="")`;
- `configure_browser_tool("", "")` or local mode;
- the loopback opt-in for a local server;
- `OLLAMA_HOST` for a LAN or tailnet Ollama.

The opt-in is ADR-0018 §4's one shared row, and `configure_llm` stays its only writer (OC3).

**D5. Test migration to the network edge.** About 146 module-level patches stop intercepting once calls go through
a client: `knowledge_base.{web,llm,vision}.httpx.*` on about 50 + 30 + 16 lines, plus the `is_private_ip` patches.
Without migration, a test would leak to the real network. Each module's tests move to the existing fixtures before
its mechanism changes, in a test-only commit that is green before and after:
- `http_requests`, with handlers per logical host;
- `fake_dns`, fail-closed;
- a new `public_net` fixture that lets `203.0.113.0/24` and `2001:db8::/32` play the public internet (Python counts
  documentation ranges as non-global).

The recipe:
- a mocked response becomes a handler;
- a mocked redirect becomes a real 3xx with `Location`;
- a kwargs assertion becomes a request assertion (`follow_redirects=False` becomes "one 302 is recorded and raises
  `HTTPStatusError`"; `timeout=` is read from `request.extensions["timeout"]`);
- `example.com` and `other.com` in `tests/test_ingest.py` are answered by a module fixture, so the suite stops
  resolving them over real DNS.
- The 18 `knowledge_base.utils.is_private_ip` patches in `tests/test_llm.py` go inert once requests are pinned, and
  move to `fake_dns` in the same way. The 6 in `tests/test_embeddings.py` stay: they patch the embedder's
  `validate_base_url` path, which this plan leaves alone.

**D6. Residuals, stated in the docs, with INF-11 behind each.**
1. The Playwright browser's own network: page JavaScript, subresources, its own lookups and redirects. It is off in
   the deployment.
2. The CDP endpoint is not pinned. Playwright connects out of process, but an IP literal (or `localhost` under the
   opt-in) gives it no lookup to rebind (OC8).
3. Three forms count as global in Python 3.12: NAT64 (`64:ff9b::/96`), IPv4-compatible (`::a.b.c.d`) and
   IPv4-translated (`::ffff:0:a.b.c.d`). Reaching their embedded IPv4 address needs a translator or tunnel on the
   path, and the deployment has none (OC5).
4. The embedding path validates non-declared hosts without pinning. Its URL is operator-declared since #569.
5. The list protects only the addresses the operator lists.

### Owner calls (each with its consequence)

- **OC1. Variable contract.** `KNOWLEDGE_BASE_HOST_ADDRESSES`, accepting networks. dutiona/infra#81 must set exactly
  this name, and a misspelt name protects nothing, invisibly. Recommended: infra#81 lists the public IPv4 address
  and the IPv6 /64, and adds a must-fail `ingest_url` probe of the host's own address.
- **OC2. Proxies.** Floored requests ignore the proxy variables, so URL ingest, image downloads and stored service
  URLs stop working behind a mandatory egress proxy; operator endpoints still use it. Recommended: accept.
- **OC3. The loopback opt-in keeps one writer, `configure_llm_tool`**, which needs `KNOWLEDGE_BASE_LLM=on`, even
  though vision, OmniParser and CDP rows now honour it. A localhost CDP or vision row therefore needs LLM switched
  on once to set the flag. Recommended: add no parameter; one prompt-injected call could otherwise open loopback
  for the CDP endpoint. Alternative: mirror the parameter on the three configure tools.
- **OC4. `configure_vision_tool(base_url="")` clears the stored URL.** Today it is a no-op pinned by a test, and
  clearing is the tool's only way out of a refused row. Recommended: accept.
- **OC5. NAT64.** Recommended: accept residual 3. Alternative: judge a `64:ff9b::/96` answer by its embedded IPv4
  address, for three lines and one test.
- **OC6. No LAN or tailnet path for a tool-stored service URL.** OmniParser on a GPU node, a CDP sidecar on a
  container network, and a vision or LLM server not reachable through `OLLAMA_HOST` are all refused. The complete
  alternative is one operator-declared endpoint variable per service, exempt by exact host like the embedder.
  Recommended: tell me which of these you run; nothing is added speculatively.
- **OC7. The ADR-0018 amendment (item 10) is a contract change.** Approve its text with the PR.
- **OC8. A CDP endpoint must be an IP literal, or `localhost` under the opt-in.** A hostname is refused, because
  Playwright would resolve it again after KB's check, and that second answer cannot be bound. Consequence: a hosted
  browser service reached by name (`wss://<vendor host>`) is refused, and so is a CDP sidecar by container name
  (already private). Recommended: accept, since the browser is off in the deployment. Alternative: accept the
  rebinding window for named endpoints as a stated residual.

### Items

- [ ] **0. Worktree and plan.** The worktree for branch `fix/572-redirect-hop-ssrf` already exists, is locked, and
  starts at origin/master `bd0fee3`, which holds #569 and #582. All work happens there.
  - Commit the plan and its review evidence (`-altitude.md`, `-advisor.md`, `-subagent-review.md`). The `-drafts/`
    directory is deleted before the first `git add`. `/finish-pr` removes all of them in its last planned commit.
  - No executable behaviour. `git status --short` is empty after the commit.
  - Commit: `docs(plans): plan per-hop SSRF validation and pinning (#572)`

- [ ] **1. Read the host's own addresses.** Files: `src/knowledge_base/settings.py`, `tests/test_settings.py`,
  `tests/conftest.py`.
  - Red tests:
    - `test_host_addresses_unset_is_empty`;
    - `test_host_addresses_parse`: `" 198.51.100.7 , 2001:db8:7::/64 "` gives the /32 and the /64;
    - `test_host_addresses_refuse_malformed`, over `198.51.100.7,,2001:db8::7`, `self.example.test`,
      `198.51.100.7/24` and `::ffff:198.51.100.7`, each a `ValidationError` matching `^KNOWLEDGE_BASE_HOST_ADDRESSES`;
    - `test_check_environment_validates_host_addresses`.

    `uv run pytest tests/test_settings.py -q -k host_addresses` fails at import.
  - Change:
    - `HOST_ADDRESSES_VAR`;
    - `host_networks() -> tuple[IPv4Network | IPv6Network, ...]`, implementing D3;
    - `check_environment()` calls it;
    - both names go in `__all__`;
    - `_declared_environment` deletes the variable, so a developer's shell cannot leak a list in.
  - Green: `uv run pytest tests/test_settings.py -q`, all passed.
  - Commit: `feat(settings): read the host's own addresses from KNOWLEDGE_BASE_HOST_ADDRESSES`

- [ ] **2. The floor refuses the host's own addresses.** Files: `src/knowledge_base/utils.py`,
  `tests/test_utils.py`, `tests/conftest.py`.
  - Red tests, with `fake_dns`, `public_net` and the list `203.0.113.7,2001:db8:7::/64`:
    - refused: the literals `203.0.113.7`, `::ffff:203.0.113.7` and `2001:db8:7::abcd`, and a name answering
      `[203.0.113.10, 203.0.113.7]`. Each makes `is_private_ip` return True and `validate_base_url` raise matching
      `private`;
    - the control `203.0.113.8` is accepted. It proves the list, not the floor, refused the others.

    - an ordinary IPv4 answer and an ordinary IPv6 answer are both classified without error;
    - a failed lookup raises the distinct message below.

    `uv run pytest tests/test_utils.py -q -k host_address` fails on the assertions.
  - Change:
    - `_is_public(ip)` returns `ip.is_global`; it is the `public_net` patch point;
    - `_refused(ip)` first narrows an IPv6 address carrying `ipv4_mapped` to that IPv4 address. It branches on the
      version first, because `IPv4Address` has no `ipv4_mapped`. It then refuses when not `_is_public`, or when
      inside `host_networks()`;
    - `_accepted_addresses(host) -> list[IPv4Address | IPv6Address]` is the D2 resolution rule. It raises
      `ValidationError` on a refusal or a failed resolution;
    - `is_private_ip` becomes "`_accepted_addresses` raised". It keeps its fail-closed contract, and
      `validate_base_url` keeps calling the module-level `is_private_ip` (`tests/test_embeddings.py` patches it).
    - Two messages:
      - `URL points to a private, reserved or this host's own address ({host}). Use a public IP or hostname.` The
        existing `match="private"` tests hold;
      - `cannot resolve {host}`, for a lookup failure. It is still fail-closed, and a resolver outage is no longer
        misreported as a private address.
    - The `public_net` fixture is created here, in `tests/conftest.py`, because this item's red tests use it.
  - Green: `uv run pytest tests/test_utils.py tests/test_ingest.py tests/test_llm.py tests/test_embeddings.py -q`,
    all passed. With the list unset, behaviour is unchanged.
  - Commit: `feat(utils): refuse the host's own addresses on the SSRF floor`

- [ ] **3. A client that floors and pins every request.** Files: `src/knowledge_base/utils.py`,
  `tests/conftest.py`, `tests/test_utils.py`, `pyproject.toml` and `uv.lock` (dev group: `trustme`).
  - Red tests (`-k pinned`), with `http_requests`, `fake_dns` and `public_net`:
    - the name stays on the request: `https://page.example.test:8443/a?b=1` is sent as
      `https://203.0.113.10:8443/a?b=1`, with `Host: page.example.test:8443`, `sni_hostname == "page.example.test"`,
      and `response.url` the original;
    - an IPv6 answer is bracketed;
    - a mixed answer `[203.0.113.10, 100.64.0.7]` is refused, and nothing is recorded;
    - fallback: an answer of `[2001:db8::10, 203.0.113.10]`, with a `ConnectError` on the first, gives 200 after
      both targets;
    - the resolved name is the IDNA 2008 one: `https://faß.example.test/` looks up `xn--fa-hia.example.test`, and
      `sni_hostname == "xn--fa-hia.example.test"`;
    - `egress_client(floor=True, proxy=...)` (and `mounts=`, `transport=`, `limits=`, `verify=`) raises `TypeError`;
    - `with egress_client(floor=True)` closes the inner transport;
    - the loopback opt-in passes `localhost` and `127.0.0.1` unpinned with no lookup, while `loop.example.test`
      answering `127.0.0.1` stays refused;
    - a 302 to `ftp://…` raises `ValidationError` matching `scheme`;
    - `egress_client(floor=False)` makes no lookup.
  - Real-socket tests on `127.0.0.1`, with `_accepted_addresses` patched to return `[127.0.0.1]`. The certificates
    come from `trustme`, a new dev-group dependency (`uv add --group dev trustme`; httpx's own suite uses it). Its
    CA is trusted through the `SSL_CERT_FILE` environment variable, which httpx's default context reads, so no
    `verify=` argument is needed:
    - `test_pinned_tls_verifies_the_name`: the leaf is issued for `a.example.test`, the server's `sni_callback`
      records `["a.example.test"]`, and the GET succeeds;
    - `test_pinned_tls_refuses_a_certificate_for_another_name`: the leaf is issued for `b.example.test`, and the
      GET for `a.example.test` raises `httpx.ConnectError` (certificate verify failed). This proves pinning by
      address does not weaken hostname verification;
    - `test_pinned_hosts_never_share_a_connection`: HTTP/1.1 requests for `a.example.test`, then `b.example.test`,
      arrive on two client ports.
  - Red: the suite fails at import first. After the change, each socket test is watched failing once against the
    weakening that targets it:
    - the SNI test, against a transport that sends no `sni_hostname`;
    - the mismatch test, against an inner transport whose SSL context has `check_hostname = False`. Dropping SNI
      would not do here: verification would then check the IP, and the mismatch test would stay green;
    - the no-reuse test, against default limits. Each runs as a script that copies `utils.py` into the
    git dir, injects the fault, runs the one test and restores from that copy, with `cmp` proof. This is
    super-plan's weaken-and-restore recipe; never `git restore`, which would discard the uncommitted change.
  - Change:
    - `_PinnedTransport` implements D2;
    - `egress_client` goes in `__all__`;
    - `HttpRecorder` changes:
      - it dispatches handlers by the logical host. The `Host` header is parsed as an authority,
        `httpx.URL(f"http://{host_header}").raw_host.decode("ascii")`. That is the ASCII form, so an IDNA name is
        keyed by its `xn--` form: `.host` would decode it back to Unicode. The parse drops the port and the IPv6 brackets; a bare `Host` value
        is not a URL. `request.url.host` is the fallback. A conftest self-test covers `page.example.test:8443`,
        `[2001:db8::10]:8443` and a portless name;
      - `hosts()` returns logical hosts, so the existing `hosts() == ["page.example.test"]` assertions
        (`tests/test_ingest.py` ~4289, ~4314) keep their meaning;
      - new: `targets()` returns `request.url.host`, the connect target, and `urls()` returns the logical URLs;
    - `fake_dns` also accepts a sequence of answer lists (lookup n gets list n, the last repeats) and records
      `lookups`.
  - Green: `uv run pytest tests/test_utils.py -q`, all passed; the full suite is unchanged, because there are no
    callers yet.
  - Commit: `feat(utils): add a client that floors and pins every request`

- [ ] **4. Fake page and image servers at the transport (test-only).** Files: `tests/test_ingest.py`,
  `tests/test_indexer.py`.
  - The D5 recipe. `test_ingest_url_uses_response_url_for_base` becomes a real 301 from `/old` to `/new`, and the two
    #569 tests drop their `is_private_ip` patch for `fake_dns`.
  - Check: on the unchanged source, the pass count equals the count recorded before the edit, and
    `rg -n 'knowledge_base\.web\.httpx' tests/` prints nothing.
  - Commit: `test(web): fake page and image fetches at the transport`

- [ ] **5. Validate and pin every hop of URL ingest and image downloads.** Files: `src/knowledge_base/web.py`,
  `tests/test_web_redirects.py` (new), `tests/test_ingest.py`.
  - Test setup: the list is `203.0.113.7,2001:db8::7`, and `page.example.test`, `img.example.test` and
    `cdn.example.test` answer public test addresses.
  - Red tests:
    - `test_a_page_redirect_into_a_refused_address_is_never_sent[target]`: `ingest_url` raises matching `private`,
      `targets()` lists only the first hop, and no chunk is stored. The targets:
      - `tailnet.example.test`, answering `100.64.0.7` and `fd7a:115c:a1e0::7`;
      - `http://100.64.0.7/`;
      - `http://127.0.0.1:8098/`;
      - `http://[::1]/`;
      - `localhost`, with `fake_dns["localhost"] = ["127.0.0.1", "::1"]`, so the case tests the floor and not the
        fail-closed resolver;
      - `host.containers.internal`, answering `169.254.1.2`, `10.0.2.2`, or `203.0.113.7`;
      - `https://203.0.113.7/`;
      - `https://[::ffff:203.0.113.7]/`;
      - `self.example.test`, answering `2001:db8::7`;
    - `test_an_image_redirect_into_a_refused_address_is_never_sent[target]`, with vision on and `_vision_call`
      patched: `_extract_html_images` returns 0 and the target is never in `targets()`;
    - `test_a_host_whose_second_resolution_is_tailnet_is_refused`: the first lookup answers `203.0.113.13`, `/a`
      returns 301 to `/b` on the same host, and the second lookup answers `100.64.0.7`. The result is a refusal,
      `targets() == ["203.0.113.13"]` and two lookups;
    - `test_a_hop_connects_to_the_address_it_validated`: one lookup, one target, the `sni_hostname` is the name;
    - `test_public_redirects_are_followed`: http→https, then to `cdn.example.test`. Chunks are added, and both
      addresses are targeted;
    - `test_a_redirect_to_a_non_http_scheme_is_refused`.

    `uv run pytest tests/test_web_redirects.py -q` fails: the targets are requested by name.
  - Change:
    - `ingest_url` keeps its scheme and hostname checks and fetches with
      `with egress_client(floor=True, follow_redirects=True, timeout=30.0) as client: client.get(url)`. Delete the
      `is_private_ip` pre-check, the post-redirect check, and the "#232 custom transport" comment. A refusal
      propagates as `ValidationError`.
    - `_extract_html_images` opens one floored client for its loop and calls `client.stream("GET", image_url)`. The
      post-redirect check goes; `_validate_image_url` stays as the pre-filter that keeps refused candidates out of
      the 10-image cap.
    - Delete `test_extract_html_images_rejects_redirect_to_private`; the new image tests replace it.
  - Green: `uv run pytest tests/test_web_redirects.py tests/test_ingest.py tests/test_indexer.py
    tests/test_embeddings.py -q`, all passed.
  - Commit: `fix(web): validate and pin every redirect hop of URL ingest and image fetches`

- [ ] **6. Fake LLM, vision and OmniParser servers at the transport (test-only).** Files: `tests/conftest.py`,
  `tests/test_llm.py`, `tests/test_vision.py`.
  - The D5 recipe, plus:
    - an `operator_ollama` fixture sets `OLLAMA_HOST=http://ollama.example.test:11434` and resets
      `embeddings._OLLAMA_URL`;
    - `api.anthropic.com` and other real names become `*.example.test`;
    - OmniParser handlers serve `127.0.0.1`.
  - Check: the pass count is unchanged on the unchanged source, and
    `rg -n 'knowledge_base\.(llm|vision)\.httpx|knowledge_base\.utils\.is_private_ip' tests/test_llm.py
    tests/test_vision.py` prints nothing.
  - Commit: `test(llm,vision): fake provider calls at the transport`

- [ ] **7. Every stored LLM URL keeps the floor, Ollama included, and is pinned.** Files:
  `src/knowledge_base/utils.py`, `llm.py`, `extraction.py`, `embed_swap.py`, `routes/extraction.py`,
  `tests/test_llm.py`, `tests/test_extraction.py`.
  - Red tests:
    - `test_a_stored_ollama_url_keeps_the_floor[url]`: a tailnet name, a listed address, and `http://localhost:11434`
      without the opt-in are each refused, and nothing is persisted;
    - `test_a_loopback_ollama_url_needs_the_opt_in` replaces `test_ssrf_check_skipped_for_ollama`;
    - `test_a_stored_row_from_before_is_refused_at_use`: a row inserted by SQL makes `_llm_call(conn=…)` raise, with
      `http_requests == []`;
    - `test_the_auto_detected_ollama_is_not_floored`: with `operator_ollama`, there is no lookup;
    - `test_a_stored_url_rebinding_at_use_is_refused`: `openai_compat`, public at write and tailnet at use;
    - `test_the_map_phase_client_is_floored` in `tests/test_extraction.py`.

    `uv run pytest tests/test_llm.py tests/test_extraction.py -q -k "floor or opt_in or auto_detected or
    rebinding"` fails.
  - Change:
    - `utils.loopback_opt_in(conn) -> bool` replaces the three inline row reads (two in `llm.py`, one in
      `embed_swap.py`);
    - `_get_llm_config` adds `"floor": base_url_row is not None`;
    - `configure_llm` normalizes every family's URL, then calls `validate_base_url(base_url,
      allow_loopback=loopback)` before persisting. The ollama scheme-only branch goes;
    - `_llm_call` uses the caller's `client` or opens `egress_client(floor=cfg.get("floor", True),
      allow_loopback=cfg.get("allow_loopback", False))` for the call (fail-closed defaults). Its call-time
      `validate_base_url` calls and the "localhost-trusted by family" and #232 comments go;
    - `_test_llm_connectivity(provider, base_url, api_key=None, *, floor: bool, allow_loopback: bool = False)` probes
      through `egress_client(floor=floor, ...)`. `configure_llm` passes `floor=cfg["floor"]`, and
      `anthropic_compat` keeps validate-and-assume;
    - `_extract_map_reduce`'s shared client becomes `egress_client(floor=cfg["floor"],
      allow_loopback=cfg["allow_loopback"])`, with no `limits`: the `ThreadPoolExecutor` bounds concurrency to
      `effective_workers`, and with keep-alive off each in-flight request holds exactly one connection;
    - the `configure_llm_tool` docstring uses `https://llm.example.test`.
  - Green: `uv run pytest tests/test_llm.py tests/test_extraction.py tests/test_routes_extraction.py
    tests/test_embed_swap.py -q`, all passed.
  - Commit: `fix(llm): keep the SSRF floor on every stored LLM URL, ollama included, and pin its requests`

- [ ] **8. Stored vision and OmniParser URLs keep the floor and are pinned.** Files:
  `src/knowledge_base/vision.py`, `src/knowledge_base/web.py` (three `_vision_call` sites), `routes/extraction.py`,
  `tests/test_vision.py`, `tests/test_multi_figure.py`, `tests/test_routes_extraction.py`.
  - Red tests:
    - `test_configure_vision_keeps_the_floor[url]`: loopback without the opt-in, and a tailnet name, are refused and
      nothing is stored. With the opt-in row, loopback is stored;
    - `test_configure_vision_empty_clears_the_stored_url` replaces `test_configure_vision_empty_base_url_is_noop`;
    - `test_a_stored_vision_row_is_refused_at_use`: `_vision_call(b64, p, **_get_vision_config(conn))` raises, and
      nothing is requested;
    - `test_the_auto_detected_vision_endpoint_is_not_floored`;
    - `test_configure_omniparser_keeps_the_floor`;
    - `test_a_refused_omniparser_row_falls_back`: the health check returns False with no request and logs a warning;
    - `test_the_auto_started_omniparser_is_not_floored`;
    - `test_a_stored_row_equal_to_the_auto_url_is_floored`: a row `http://127.0.0.1:7862` with the opt-in off is
      refused at use;
    - `test_an_opted_in_loopback_omniparser_row_reaches_parse`: with the opt-in on, a row `http://127.0.0.1:7863`
      serves both `/health` and `/parse` through `_run_omniparser`.

    `uv run pytest tests/test_vision.py -q -k "floor or clears or omniparser"` fails.
  - Change:
    - `_get_vision_config` adds `"floor"` and `"allow_loopback"`;
    - `configure_vision`: `""` deletes the row, and any other value is normalized and floored before anything is
      written;
    - `_vision_call(image_b64, prompt, *, base_url, model, floor, allow_loopback)` posts through `egress_client`.
      The three `web.py` callers pass `**vision_config`, and `_dispatch_vision_calls` forwards it. The four
      `fake_vision_call(image_b64, prompt, *, base_url, model)` fakes in `tests/test_multi_figure.py` (~297–418)
      take `**_config`;
    - OmniParser endpoints carry provenance (D4):
      - `_OMNIPARSER_AUTO_URL` replaces the three `f"http://127.0.0.1:{...}"` sites (~390, ~2093, ~2149);
      - `_resolve_omniparser_server_url(conn, path)` returns the endpoint mapping or None. Its `floor` is
        `configured row is not None`, and its `allow_loopback` is `loopback_opt_in(conn)`;
      - one helper, `_omniparser_endpoint(conn)`, returns the stored row as a floored mapping (with the opt-in),
        or the auto-start endpoint `{"base_url": _OMNIPARSER_AUTO_URL, "floor": False, "allow_loopback": False}`.
        Both ETA sites (~2091 and ~2147, which read the row directly today) and `_resolve_omniparser_server_url`
        use it. `_ensure_omniparser_server`'s two health checks (~393, ~400) pass the auto mapping;
      - the mapping replaces the `server_url` parameter of `_run_omniparser_pipeline`, `_run_omniparser` and
        `web._extract_web_figures` (~805), down to `_check_omniparser_server(**endpoint)` and
        `_run_omniparser_http(png, **endpoint)`;
      - both helpers send through `egress_client(floor=floor, allow_loopback=allow_loopback)`. On a
        `ValidationError` they return False or None, with a warning naming the sanitized URL;
    - `configure_omniparser` normalizes a non-empty `server_url` (`rstrip("/")`) and floors it, with the opt-in,
      before writing;
    - the docstrings drop the `localhost` and `gpu-node` examples.
  - Green: `uv run pytest tests/test_vision.py tests/test_multi_figure.py tests/test_ingest.py
    tests/test_vision_omniparser_integration.py tests/test_routes_extraction.py -q`, all passed.
  - Commit: `fix(vision): keep the SSRF floor on stored vision and OmniParser URLs and pin their requests`

- [ ] **9. The CDP endpoint keeps the floor.** Files: `src/knowledge_base/web.py`,
  `src/knowledge_base/routes/ingestion.py`, `tests/test_ingest.py`.
  - Red tests:
    - `test_configure_browser_keeps_the_floor[endpoint]`: each of these is refused, and nothing is stored:
      - `ws://100.64.0.7:3000`;
      - the listed `ws://203.0.113.7:3000`;
      - `ws://localhost:3000` without the opt-in;
      - a public name `wss://cdp.example.test`, refused because it is a name (OC8);
    - `ws://203.0.113.10:3000` (public, under `public_net`) is stored;
    - the existing `ws://localhost:3000` tests set the opt-in row;
    - `test_a_refused_stored_cdp_endpoint_disables_the_fallback`: `_get_browser_config` returns None and logs a
      warning.

    `uv run pytest tests/test_ingest.py -q -k browser` fails.
  - Change: `_check_cdp_endpoint(endpoint, *, allow_loopback)` keeps the `ws`/`wss` check and message. It then
    requires the host to be an IP literal, or the exact label `localhost` under the opt-in; any other name raises
    `the CDP endpoint must be an IP address (or localhost with allow_loopback_base_url)`. Finally it runs
    `validate_base_url` on the URL with `ws`→`http` and `wss`→`https`. `configure_browser` calls it before writing,
    and `_get_browser_config` calls it on a stored endpoint, both with `loopback_opt_in(conn)`.
  - Green: `uv run pytest tests/test_ingest.py tests/test_routes_ingestion.py -q`, all passed.
  - Commit: `fix(web): keep the SSRF floor on the CDP endpoint`

- [ ] **10. Document the floor, the list and the amendment.** Files: those in Documentation.
  - No executable behaviour. `rg -n KNOWLEDGE_BASE_HOST_ADDRESSES README.md docs/` lists the README, the
    installation page, the ingesting page and the ADR.
  - Commit: `docs: describe the per-request SSRF floor and KNOWLEDGE_BASE_HOST_ADDRESSES`

- [ ] **11. Boy-scout: stale tool docs, one commit each.** No executable behaviour.
  - `docs/usage/structured-extraction.md` names an `env:ANTHROPIC_API_KEY` key spec, which #569 refuses; use
    `env:KNOWLEDGE_BASE_LLM_API_KEY`. Commit: `docs(extraction): name the only env: source an LLM key accepts`
  - `docs/reference/mcp-tools.md`:
    - `configure_llm_tool` lacks `anthropic_compat` and `allow_loopback_base_url`, and its example
      `http://192.168.1.41:1234` has been refused since #190;
    - `configure_omniparser_tool` lacks `server_url`;
    - `docs/reference/schema.md` omits `omniparser_server_url`.

    Commit: `docs(reference): document every configure tool parameter`
  - `docs/usage/figure-extraction.md`: "inline extraction is skipped" is false, since `ingest_url` always runs
    `_extract_html_images`; and `source_url` is the requested URL, not the post-redirect one. Commit:
    `docs(vision): correct when inline image extraction runs and what source_url holds`

- [ ] **12. Gates, then the PR.** Run Verification at the final clean HEAD and push.
  - The PR body opens with:
    - `Closes #572`
    - `Refs dutiona/infra#37`
    - `Refs #232` (the `[super-qa]` umbrella that carries this redirect finding stays open)
    - `Refs dutiona/infra#81` (its quadlet sets `KNOWLEDGE_BASE_HOST_ADDRESSES`)
  - Then: `Classification: Security patch (user-confirmed)`, the D4 table, the remedies, OC1–OC7 and their answers,
    and the Collateral findings section.
  - No closing keyword appears near #232, not even negated.
  - Check: `gh pr view <N> --json closingIssuesReferences` lists only 572.

- [ ] **13. `/super-review`** at the confirmed Full gate. Each finding gets its own commit, failing test first when
  it has behaviour, and the gates are re-run after the last one.

- [ ] **14. `/finish-pr`; the owner merges.** The plan issue is published once by
  `~/.claude/skills/finish-pr/scripts/archive-plan-issue.sh`. If an approval handoff already published it, that
  issue is the archive. The working plan leaves the branch in finish-pr's last planned commit. The owner
  squash-merges; the worker never merges.

## Documentation

- `README.md`:
  - a configuration-table row for `KNOWLEDGE_BASE_HOST_ADDRESSES` (optional; comma-separated addresses or CIDR
    networks of this host, refused by the SSRF floor though public);
  - the paragraph after the table: the floor is checked on every request, redirect hops included, and operator
    endpoints are trusted;
  - "Upgrading": stored service URLs keep the floor, with the D4 remedies.
- `docs/getting-started/installation.md`, container section: set the list to the host's public IPv4 address and
  IPv6 /64.
- `docs/usage/ingesting-documents.md`:
  - Web Pages: redirects are followed, every hop is floored and pinned, there is no proxy, and the browser fallback
    fetches outside the floor (D6.1);
  - the image SSRF bullet: every hop;
  - the "SSRF safety" paragraph adds the list and the stored URLs. The D5 sentence stays word for word.
- `docs/usage/structured-extraction.md`: a stored Ollama `base_url` keeps the floor; local Ollama needs no
  `base_url` or the opt-in, and LAN or tailnet Ollama uses `OLLAMA_HOST`.
- `docs/usage/figure-extraction.md`:
  - the `configure_vision_tool` example drops `base_url`;
  - `""` clears;
  - stored URLs keep the floor;
  - an OmniParser `server_url` must be public or tunnelled to `127.0.0.1:7862`;
  - the image bullet covers every hop.
- `docs/reference/mcp-tools.md`:
  - `ingest_url`: every hop is floored;
  - `configure_vision_tool` and `configure_omniparser_tool`: the floor and the opt-in;
  - `configure_browser_tool`: the floor, the opt-in, and the IP-literal rule (OC8). Its `ws://localhost:3000`
    example (~668) says it needs the opt-in.
  - The distinct `cannot resolve` message is promised for request-time lookups only; a write-time
    `validate_base_url` failure keeps the floor's message.
- `docs/reference/schema.md`: `allow_loopback_base_url` covers every stored service URL, never ingest or image URLs.
- `docs/design/adr/0018-provider-abstraction.md`: "Amendment (2026-11-16, #572)", three bullets:
  1. each request, hops included, resolves once, is refused unless every answer passes, is sent to an accepted
     answer with TLS on the name, and uses no proxy;
  2. the floor also refuses `KNOWLEDGE_BASE_HOST_ADDRESSES`, with IPv4-mapped answers judged as IPv4;
  3. every URL a tool stores keeps the floor with the shared opt-in, carried as provenance and not inferred from its
     value; operator endpoints do not. A CDP endpoint must be an IP literal, or `localhost` under the opt-in.
- `CLAUDE.md` gets a domain row "SSRF floor and pinned client | `src/knowledge_base/utils.py` |
  `docs/usage/ingesting-documents.md`". `AGENTS.md` rule 6 says: fake outbound HTTP with `http_requests`, `fake_dns`
  and `public_net`, and never patch `httpx` inside a module.

## Testing

- **Unit tests** (`tests/test_settings.py`, `tests/test_utils.py`): the grammar and the startup refusal; the floor
  rule (literals, mapped forms, networks, any-answer refusal, with a non-vacuous control); the transport (name kept,
  IPv6, mixed answers, fallback, IDNA, opt-in, scheme, plain client).
- **Real-socket tests** on loopback only, with `trustme` certificates: SNI carries the name; a certificate for the
  name verifies; a certificate for another name is refused; two names never share a connection. Each is watched
  failing once against a weakened transport.
- **Acceptance tests** (`tests/test_web_redirects.py`):
  - every redirect target the issue names, plus IPv6, mapped and network forms of the host's addresses;
  - `host.containers.internal` under three runtimes;
  - the second resolution;
  - the validated address is the one connected;
  - public redirects still work;
  - images follow the same rules.
- **Policy tests** (`tests/test_llm.py`, `tests/test_extraction.py`, `tests/test_vision.py`, `tests/test_ingest.py`):
  write-time refusals; refusals at use for pre-existing rows; operator defaults unfloored; the opt-in; `""` clears;
  the OmniParser fallback; CDP disabled.
- **Migrated tests** keep their meaning at the transport, proven green on the unchanged source before each swap.
- Not tested: verification against a public CA over the internet (the smoke below exercises it, not as a gate).
  The Playwright network is a stated residual.

## Verification

- `uv lock --check`: exit 0 (the lock carries the new dev dependency `trustme`).
- `ruff check src/ tests/`: `All checks passed!`
- `ruff format --check src/ tests/`: exit 0.
- `uv run pytest tests/ -q -m "not slow"`: all passed, 0 failed, 0 errors.
- `uv sync --all-groups && uv run basedpyright src/ tests/`: 0 errors.
- `rg -n 'knowledge_base\.(web|llm|vision)\.httpx' tests/` prints nothing.
- `rg -n 'httpx\.(get|post|stream)\(' src/knowledge_base/{web,llm,vision}.py` prints nothing.
- Hermetic run, where unprivileged user namespaces exist:
  `unshare -rn sh -c 'ip link set lo up && uv run --offline pytest tests/ -q -m "not slow"'` passes. Any test that
  still needs the network is reported, not hidden.
- Startup refusal: with the suite's four `KNOWLEDGE_BASE_EMBED_*` values exported and a scratch `KNOWLEDGE_BASE_DB`,
  `KNOWLEDGE_BASE_HOST_ADDRESSES=198.51.100.7/24 uv run knowledge-base </dev/null; echo $?` prints
  `knowledge-base: KNOWLEDGE_BASE_HOST_ADDRESSES ...`, then `78`.
- Real-network smoke (not a gate):
  - `egress_client(floor=True, follow_redirects=True, timeout=10).get("http://github.com/")` prints
    `200 https://github.com/` with one hop in `history`. This is a CA-verified TLS connection on a pinned address;
  - `.get("http://localhost/")` raises the floor's `ValidationError`.

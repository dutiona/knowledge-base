# [Codex Review] plan review, headless thread 01a1e613-8c9c-7e30-9324-f95b9e356efb (configured default model; no model override in the transport)

## Round 1

Michaël, **the plan needs revision before implementation**. Confidence is high in the findings below.

The altitude report matches the plan digest. Its scope and level of detail are justified by the owner-approved expansion across several call sites; I found no duplication that warrants reducing the plan’s altitude. The required Documentation, Testing, and Verification sections are present.

Altitude report SHA-256: 062da951ae911af126207d7ecf80d0ceb4c90d30064d7f613511296bda308367

- **[HIGH] D4, D6.2, item 9 — CDP remains vulnerable to rebinding.** `_get_browser_config` validates a hostname, but Playwright subsequently resolves and connects independently. A hostname that returns a public address during validation and a tailnet address during Playwright’s lookup reaches the refused target. This conflicts with ADR-0018 §4’s actual-connect-target contract; the proposed amendment in Documentation does not explicitly authorize this exception. Bind Playwright’s connection to the validated address or refuse configurations for which that cannot be guaranteed, and test the second-resolution case.

- **[HIGH] D4, item 8 — OmniParser trust is inferred from URL equality rather than provenance.** The planned `floor=base_url != _OMNIPARSER_AUTO_URL` treats a previously stored `http://127.0.0.1:7862` row as auto-started and unfloored. Such a row can already exist because the current configuration checks only the scheme. It would bypass the shared loopback opt-in at use, contrary to D4. Carry whether the URL came from the stored row through resolution, health checks, and requests; test a pre-existing row equal to the auto URL with the opt-in off.

- **[HIGH] Item 2 — The proposed mapped-address expression fails for IPv4.** `ip.ipv4_mapped or ip` accesses an attribute of `IPv6Address` that `IPv4Address` does not provide. Ordinary IPv4 answers would raise `AttributeError` instead of being classified. Branch on address family before inspecting `ipv4_mapped`; retain tests for both ordinary IPv4 and mapped IPv6.

- **[MEDIUM] D2, item 3, Testing — TLS hostname verification is not established by the planned gate.** The socket test checks SNI only, while certificate verification is relegated to a real-network smoke explicitly marked “not a gate.” I think the proposed `sni_hostname` extension can control hostname verification in the pinned versions, but the plan does not demonstrate it. Add a gated local TLS test with a trusted test certificate for the logical hostname, plus a mismatched-name negative control. This also verifies that connecting to the pinned IP does not silently weaken certificate checks.

- **[MEDIUM] D2, item 7 — The pinned transport discards the map phase’s connection limit.** D2 constructs its inner transport with only `max_keepalive_connections=0`, while item 7 passes the existing `limits=...` to `egress_client`. A client-level limit does not necessarily configure an explicitly supplied transport; I think the inner pool would retain its default connection limit. Make the transport honor the requested maximum while forcing zero keep-alive connections, and verify concurrent map calls respect it.

This is a read-only plan review. I did not run project tests, gates, builds, or network smoke checks.

REVIEW COMPLETE

## Round 2

Michaël, the round-2 delta resolves the provenance, IPv4 classification, connection-limit, and TLS-test coverage findings. Two details still need correction. Confidence is high.

- **[MEDIUM] Item 3 — The certificate-mismatch mutation check cannot prove the stated failure.** Removing `sni_hostname` from a request pinned to `127.0.0.1` should make verification use the IP address. A certificate for `b.example.test` still fails verification, so the negative test remains green under that mutant. Use a targeted weakening that disables hostname verification, and confirm that the mismatch test then fails.

- **[MEDIUM] Item 3 — Parsing a bare `Host` header as an `httpx.URL` does not reliably extract its host.** A value such as `page.example.test:8443` has no scheme or `//` authority prefix, so it can be interpreted as a relative URL rather than an authority. Handler dispatch and logical-host assertions can consequently fail during the test migration. Parse it as an authority with an explicit scheme or `//` prefix, and cover a name with a port and a bracketed IPv6 address.

The refreshed altitude report’s first line matches the current plan digest.

Altitude report SHA-256: 6fdd2696a7538aed9eaa639169ba16d75b01146ffd4bd3dd3011d2d92523090a

REVIEW COMPLETE

## Round 3

Michaël, **LGTM for the round-3 delta**. Confidence is high: both round-2 findings are addressed in the plan. This was a read-only review; no tests were run.

The refreshed altitude report names the current plan digest.

Altitude report SHA-256: 682395fe9b427640a2b981a546630d0517f81fa23cc67609618d85521274f95e

REVIEW COMPLETE

## Round 4

Michaël, **LGTM for the round-4 delta**. Confidence is high. The refreshed altitude report names the current plan digest.

Altitude report SHA-256: 54baa26a1776daf85087bd84e0f1a19ac98e5008f6c5a0afc6d31782a01bee43

REVIEW COMPLETE

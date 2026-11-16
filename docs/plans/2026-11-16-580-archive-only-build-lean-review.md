Reviewer: Codex gpt-6.1-sol (headless, read-only sandbox, fresh thread) via codex exec (local workspace path prefix stripped from the file links, nothing else changed)

~~~~
VERDICT: CHANGES REQUESTED — Michaël, the marker is a useful checkout guard, but the plan overstates its guarantees and leaves acceptance gaps.

No BLOCKER findings.

1. **HIGH — A hash-shaped marker does not establish archive provenance.** [Plan:43](docs/plans/2026-11-16-580-archive-only-build.md:43)

   Replacing the checkout’s marker with any 40 lowercase hexadecimal characters makes the check pass, including with an untracked `.py` under `src/`. `grep -Eqx` also accepts a multiline file containing one matching line. The claim that an “edited file is refused” is false.

   Nor does `%H` establish that a tag was used: Git expands it for commit archives too. An extracted archive subsequently modified with extra sources also passes. These follow from the check and [Git’s documented substitution semantics](https://git-scm.com/docs/gitattributes#_export_subst).

   **Fix:** explicitly define this as protection against ordinary checkout builds with the tracked marker intact, and remove the universal provenance claims. If #580 requires stronger enforcement, obtain Michaël’s decision on a trusted build entry point that resolves a tag and constructs the archive itself; a context-supplied marker cannot certify the other context files. Add verification cases for an edited marker, multiline marker, untagged commit archive, and modified extracted archive.

2. **MEDIUM — Some dirty checkout failures never print the required archive command.** [Plan:39](docs/plans/2026-11-16-580-archive-only-build.md:39)

   Keeping the existing non-Python guard unchanged means a checkout containing `src/knowledge_base/credentials.txt` exits at `Containerfile:14–15`, before the new diagnostic. This directly misses the acceptance requirement that checkout refusal point at the archive command. The proposed matrix covers only Python contaminants.

   **Fix:** include the archive instruction in the existing guard’s failure message and add a non-Python dirty-checkout case. Also cover a missing marker: its `COPY` fails before the proposed `RUN` can explain recovery.

3. **MEDIUM — “It never reaches an image” is incorrect for intermediate cache images.** [Plan:37](docs/plans/2026-11-16-580-archive-only-build.md:37)

   The late refusal happens after source copying and wheel installation. With layer caching, successful preceding instructions can already have produced intermediate images containing both the dirty source and installed package. Buildah also supports pushing those cache images before a subsequent instruction fails; its [executor commits and exports cache results instruction by instruction](https://github.com/containers/buildah/blob/main/imagebuildah/stage_executor.go).

   **Fix:** say precisely that refusal prevents completion of the requested runtime image, while contaminants can remain in intermediate images and caches. If preventing that exposure is required, use the early check. Include inspection of intermediate artifacts in the implementer’s verification evidence.

4. **MEDIUM — The image-ID argument establishes cache reuse, not general image identity.** [Plan:31](docs/plans/2026-11-16-580-archive-only-build.md:31)

   The reported `2c145caf01a1` comparison supports the late check under the implementer’s shared-cache conditions. It does not establish that fresh builds retain the old image ID. Podman documents that image creation timestamps ordinarily change hashes even when sources are unchanged. The reported early-check result, `150a518cda26`, therefore does not by itself demonstrate a runtime-content difference. [Podman timestamp documentation](https://docs.podman.io/en/latest/markdown/podman-build.1.html#timestamp-seconds)

   **Fix:** define the acceptance claim explicitly as warm-cache identity, record full image IDs, builder versions, both input-image digests—including `uv:latest`—and cache-hit evidence. Add an uncached baseline-versus-baseline control before attributing ID differences to the mechanism.

   Rejecting the early check is **conditionally reasonable** if preserving the existing cached image ID is the intended criterion. Rejecting it as inherently incompatible with equivalent runtime output is not supported. My judgment here depends on the reported measurements; I did not reproduce them.

5. **MEDIUM — The context probe proves COPY filtering, not that excluded files never reach the builder.** [Plan:27](docs/plans/2026-11-16-580-archive-only-build.md:27)

   `git archive <tag>` emits the whole tracked tree, including tests and documentation, into Podman’s stdin. A `COPY . /ctx` probe establishes which files are copyable after ignore filtering. It does not establish a transport or extraction boundary. Podman describes ignore rules as exclusions applied when executing `COPY` and `ADD`. [Podman ignore-file documentation](https://docs.podman.io/en/latest/markdown/podman-build.1.html#containerignore-dockerignore)

   **Fix:** distinguish archive input from COPY-visible context in the plan and documentation. Keeping `.containerignore` addresses the stated allowlist requirement; if “doesn’t send the whole tracked tree” is literal, construct a restricted archive before feeding Podman and retain the ignore file as an additional filter.

6. **LOW — The retained release-tag prerequisite becomes inaccurate.** [Plan:73](docs/plans/2026-11-16-580-archive-only-build.md:73)

   Installation lines 114–115 currently identify any release containing `Containerfile` as sufficient. Earlier such releases lack this marker mechanism and retain the checkout-build behavior, contradicting the proposed universal refusal paragraph.

   **Fix:** require a release containing the archive-only change, and qualify the refusal explanation accordingly.

The basic annotated-tag substitution and retained allowlist are sound in principle. An archive from a raw tree object will retain the placeholder and fail, as Git documents. Archive and checkout marker contents should produce distinct COPY cache keys; a successful archive check may itself be cached, so “the check always runs” should instead say that a checkout cannot reuse that successful check.

The simpler security design is the same marker checked before copying source. Its cache cost is real according to the reported measurement; preserving that cache is the only demonstrated reason here to prefer the late check.

This was a read-only review. No files were written and no builds or tests were run. Successful stdin-tar handling, the probe listing, and image-ID equality remain implementer-reported evidence pending the final-commit matrix.

REVIEW COMPLETE
~~~~

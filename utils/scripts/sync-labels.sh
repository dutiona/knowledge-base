#!/usr/bin/env bash
# sync-labels.sh — declarative label taxonomy for knowledge-base (idempotent).
#
# Order matters (per PR #365 review):
#   1. RENAME unambiguous legacy labels FIRST (preserves issue associations).
#      Doing this before the upsert avoids `gh label edit bug --name type:bug`
#      colliding with a type:bug the upsert would have just created.
#   2. UPSERT the full taxonomy (gh label create --force). Idempotent: any label
#      already produced by step 1 is a safe no-op (color/description converge).
#   3. LEAVE ambiguous legacy labels (high/medium/low/info/enhancement/quality)
#      for the per-issue migration (migrate-issues.sh) to resolve.
#   4. DELETE noise default labels — report usage across all states first.
#
# Usage:
#   utils/scripts/sync-labels.sh --dry-run      # print every mutation, change nothing
#   utils/scripts/sync-labels.sh                # apply (prompts before each deletion)
#   utils/scripts/sync-labels.sh --yes          # apply, auto-confirm deletions

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/pm-common.sh
source "$HERE/lib/pm-common.sh"
parse_common_args "$@"
case "${PM_ARGS[0]:-}" in --help)
	sed -n '2,22p' "$0"
	exit 0
	;;
esac
preflight

# ---- 1. RENAME unambiguous legacy labels (FIRST) ----------------------------
# old|new  — both halves unambiguous; rename preserves associations on all issues.
RENAMES=(
	"database|area:db"
	"retrieval|area:search"
	"refactoring|type:refactor"
	"research|type:research"
	"security|type:security"
	"plan|type:plan"
	"bug|type:bug"
	"documentation|type:docs"
)

rename_label() {
	local old="$1" new="$2"
	if label_exists "$old"; then
		if label_exists "$new"; then
			warn "rename '$old'→'$new': target already exists; leaving '$old' in place"
			warn "  (gh cannot merge labels — relabel its issues to '$new', then delete '$old')"
		else
			run gh label edit "$old" -R "$SLUG" --name "$new"
			ok "renamed '$old' → '$new'"
		fi
	else
		log "rename '$old'→'$new': source absent (already renamed?), skip"
	fi
}

log "== step 1: rename legacy labels =="
for pair in "${RENAMES[@]}"; do
	rename_label "${pair%%|*}" "${pair##*|}"
done

# ---- 2. UPSERT full taxonomy ------------------------------------------------
# name|color|description
TAXONOMY=(
	# type: (exactly one per issue, required)
	"type:bug|d73a4a|Something is broken"
	"type:feature|a2eeef|New capability"
	"type:enhancement|0e8a16|Improve existing functionality"
	"type:perf|fbca04|Performance fix (N+1, O(n^2), batching)"
	"type:refactor|c5def5|Restructure, no behaviour change"
	"type:test|bfd4f2|Test coverage or test infrastructure"
	"type:docs|0075ca|Documentation only"
	"type:chore|e4e669|Deps, config, tooling, cleanup"
	"type:research|d876e3|Spike / design / PoC (deliverable: ADR or note)"
	"type:eval|bfdadc|Benchmark an existing tool/model (deliverable: numbers)"
	"type:security|e11d48|Security finding or hardening"
	"type:epic|3e4b9e|Umbrella tracking issue with sub-issues"
	"type:plan|d4c5f9|Implementation plan (links to PR)"
	# area: (exactly one per issue, required)
	"area:ingest|0e8a16|ingest.py, chunking.py, web.py, PDF path"
	"area:search|1d76db|search.py, keywords.py, reranker.py, folder_summaries, prediction_errors, auto_relate"
	"area:embeddings|5319e7|embeddings.py, embed_swap.py, quantization"
	"area:extraction|006b75|extraction.py, llm.py, entity resolution"
	"area:vision|e36209|vision.py, OmniParser, figures"
	"area:papers|0052cc|papers.py, bibtex.py, relationships, conclusions"
	"area:db|1d76db|db.py, _conn.py, schema, migrations"
	"area:mcp|1f883d|server.py, routes/, MCP tools, ACL"
	"area:infra|cfd3d7|CI, build, jobs, indexer CLI, serve mode, scaling, packaging"
	"area:integration|8a63d2|memory-engine hooks, four-layer architecture"
	"area:docs|0075ca|Documentation"
	# priority: (additive; scheduling urgency)
	"priority:critical|b60205|Drop everything - blocks the project"
	"priority:high|d93f0b|On the critical path to the next milestone"
	"priority:medium|fbca04|Planned work"
	"priority:low|0e8a16|Nice to have"
	# severity: (additive; super-qa findings only — intrinsic technical impact)
	"severity:critical|b60205|super-qa: critical technical impact"
	"severity:high|d93f0b|super-qa: high technical impact"
	"severity:medium|fbca04|super-qa: medium technical impact"
	"severity:low|0e8a16|super-qa: low technical impact"
	"severity:info|c5def5|super-qa: informational"
	# status: (additive; only when non-default)
	"status:blocked|e4e669|Blocked by upstream/external dependency"
	"status:needs-design|fbca04|Requires design work before implementation"
	# provenance
	"super-qa|d4c5f9|Finding from /super-qa codebase audit"
)

upsert_label() {
	local name="$1" color="$2" desc="$3"
	run gh label create "$name" -R "$SLUG" --color "$color" --description "$desc" --force
}

log "== step 2: upsert taxonomy (${#TAXONOMY[@]} labels) =="
for row in "${TAXONOMY[@]}"; do
	IFS='|' read -r name color desc <<<"$row"
	upsert_label "$name" "$color" "$desc"
done
ok "taxonomy upserted"

# ---- 3. AMBIGUOUS legacy labels: leave for migrate-issues.sh -----------------
log "== step 3: ambiguous legacy labels left in place (resolved per-issue in Phase 7) =="
for l in enhancement quality high medium low info; do
	if label_exists "$l"; then log "  keeping '$l' (migrate-issues.sh resolves → type:/priority:/severity:)"; fi
done

# ---- 4. DELETE noise default labels (report usage first) --------------------
NOISE=(duplicate invalid wontfix "help wanted" "good first issue" question)

delete_noise() {
	local name="$1"
	if ! label_exists "$name"; then
		log "noise '$name' absent, skip"
		return 0
	fi
	local n
	n="$(gh issue list -R "$SLUG" --label "$name" --state all -L 200 --json number --jq 'length' 2>/dev/null || echo '?')"
	warn "noise label '$name' is on $n issue(s) (all states)"
	if confirm "Delete label '$name'?"; then
		run gh label delete "$name" -R "$SLUG" --yes
		ok "deleted '$name'"
	fi
}

log "== step 4: delete noise default labels =="
for l in "${NOISE[@]}"; do delete_noise "$l"; done

ok "sync-labels complete (dry_run=$DRY_RUN)"

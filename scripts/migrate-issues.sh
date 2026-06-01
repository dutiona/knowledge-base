#!/usr/bin/env bash
# migrate-issues.sh — apply an approved mapping.tsv to OPEN issues (idempotent).
#
# For each row: set type:/area:/xcut labels (removing resolved legacy labels),
# add the issue to KB — Main, and set its Phase + Priority fields. Then add the
# fixed Critical-Path member list. Closed issues are never touched.
#
# Requires scripts/.pm-ids.env (from setup-projects.sh) for project/field/option
# ids — gh project item-edit targets ids, not names.
#
# Usage:
#   scripts/migrate-issues.sh --dry-run            # print every gh call
#   scripts/migrate-issues.sh                      # apply
#   scripts/migrate-issues.sh --dry-run mapping.tsv  # explicit mapping path

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/pm-common.sh
source "$HERE/lib/pm-common.sh"
parse_common_args "$@"
case "${PM_ARGS[0]:-}" in --help)
	sed -n '2,20p' "$0"
	exit 0
	;;
esac
preflight

MAP="${PM_ARGS[0]:-$HERE/mapping.tsv}"
[[ -f "$MAP" ]] || die "mapping not found: $MAP (run gen-mapping.sh first)"
# shellcheck source=/dev/null
[[ -f "$HERE/.pm-ids.env" ]] && source "$HERE/.pm-ids.env" || die ".pm-ids.env missing (run setup-projects.sh)"
[[ -n "${MAIN_NUMBER:-}" && -n "${MAIN_PID:-}" ]] || die ".pm-ids.env incomplete (no MAIN_NUMBER/MAIN_PID)"

# Legacy labels that the migration resolves per-issue (removed when superseded).
LEGACY_REMOVE=(enhancement quality high medium low info)

# option-id lookup: phase/priority value -> env var PHASE_OPT_x / PRIO_OPT_x
opt_id() { # $1=PHASE|PRIO  $2=value
	local key
	key="$1_OPT_$(printf '%s' "$2" | sed 's/[^A-Za-z0-9]/_/g')"
	printf '%s' "${!key:-}"
}

migrate_row() {
	local num="$1" type="$2" area="$3" xcut="$4" phase="$5"
	local url="https://github.com/$SLUG/issues/$num"

	# 1. labels: add type/area/xcut; remove any legacy that this row supersedes
	local add="$type,$area"
	[[ -n "$xcut" ]] && add="$add,$xcut"
	run gh issue edit "$num" -R "$SLUG" --add-label "$add"
	local rm_list=""
	for l in "${LEGACY_REMOVE[@]}"; do rm_list+="${rm_list:+,}$l"; done
	# only attempt removal of legacy labels the issue actually has (gh ignores absent, but keep quiet)
	run gh issue edit "$num" -R "$SLUG" --remove-label "$rm_list" || true

	# 2. add to Main, capture project item-id (item-edit needs the id, not the #)
	local item_id
	if [[ "$DRY_RUN" == "1" ]]; then
		run gh project item-add "$MAIN_NUMBER" --owner "$OWNER" --url "$url" --format json
		item_id="<item-id>"
	else
		item_id="$(gh project item-add "$MAIN_NUMBER" --owner "$OWNER" --url "$url" --format json --jq '.id')"
	fi

	# 3. set Phase + Priority fields by option-id
	if [[ -n "$phase" ]]; then
		local poid
		poid="$(opt_id PHASE "$phase")"
		if [[ -n "$poid" ]]; then
			run gh project item-edit --id "$item_id" --project-id "$MAIN_PID" --field-id "$PHASE_FIELD_ID" --single-select-option-id "$poid"
		else warn "#$num: no option-id for Phase '$phase' (add the option, then re-run)"; fi
	fi
	if [[ "$xcut" == priority:* ]]; then
		local pv="${xcut#priority:}"
		pv="${pv^}" # critical->Critical
		local oid
		oid="$(opt_id PRIO "$pv")"
		if [[ -n "$oid" ]]; then
			run gh project item-edit --id "$item_id" --project-id "$MAIN_PID" --field-id "$PRIORITY_FIELD_ID" --single-select-option-id "$oid"
		else warn "#$num: no option-id for Priority '$pv'"; fi
	fi
	ok "#$num → $type $area ${xcut:-—} phase=${phase:-—}"
}

log "== migrate open issues from $MAP =="
# Parse tab-separated, PRESERVING empty fields. `read` with a whitespace-only IFS
# (tab) collapses consecutive delimiters and drops empty fields, which would shift
# columns left (empty xcut → phase read as xcut, title read as phase). Splitting
# with awk into NUL-delimited records and reading each field with `IFS= read -rd ''`
# preserves empties exactly. Also strips any trailing CR from CRLF files.
parse_and_migrate() {
	local num type area xcut phase needs_review
	# awk emits, per data row, the 6 fields we use each terminated by NUL.
	while IFS= read -r -d '' num &&
		IFS= read -r -d '' type &&
		IFS= read -r -d '' area &&
		IFS= read -r -d '' xcut &&
		IFS= read -r -d '' phase &&
		IFS= read -r -d '' needs_review; do
		[[ -z "$num" ]] && continue
		[[ -n "$needs_review" ]] && warn "#$num flagged ($needs_review) — ensure mapping.tsv was reviewed"
		migrate_row "$num" "$type" "$area" "$xcut" "$phase"
	done < <(
		awk -F'\t' 'NR>1 {
			gsub(/\r/,"")                       # strip CR (CRLF files)
			printf "%s\0%s\0%s\0%s\0%s\0%s\0", $1,$2,$3,$4,$5,$6
		}' "$MAP"
	)
}
parse_and_migrate

# ---- Critical-Path board: fixed membership ----------------------------------
CRITPATH=(325 326 328 275 342 107 80 262 253)
log "== populate Critical-Path board (#${CRITPATH_NUMBER:-?}) =="
if [[ -n "${CRITPATH_NUMBER:-}" ]]; then
	for n in "${CRITPATH[@]}"; do
		run gh project item-add "$CRITPATH_NUMBER" --owner "$OWNER" --url "https://github.com/$SLUG/issues/$n" --format json
	done
else warn "CRITPATH_NUMBER not in .pm-ids.env — skipping Critical-Path population"; fi

ok "migrate-issues complete (dry_run=$DRY_RUN)"
warn "Phase 7d: after verifying, delete now-unreferenced ambiguous labels:"
warn "  for l in enhancement quality high medium low info; do gh label delete \"\$l\" -R $SLUG; done"

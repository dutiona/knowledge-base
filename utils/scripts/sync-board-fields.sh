#!/usr/bin/env bash
# sync-board-fields.sh — give the secondary boards (Triage #9, Research & Eval
# #10) their own Priority / Severity / Phase single-select fields and populate
# them, so they can be sorted/grouped natively (a Projects table cannot sort by a
# label, only by a field).
#
# WHY this exists separately from setup-projects.sh: Projects fields are
# PER-BOARD, not global — the Priority on Main (#7) is a different object from a
# Priority on Triage (#9). The label (`priority:high`) is the global source of
# truth; these fields are a board-local convenience derived FROM the labels (and,
# for Phase, from Main's already-migrated value). Re-running re-syncs them, so
# the label stays authoritative and the fields never silently drift.
#
# Idempotent. Usage:
#   utils/scripts/sync-board-fields.sh --dry-run
#   utils/scripts/sync-board-fields.sh

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/pm-common.sh
source "$HERE/lib/pm-common.sh"
parse_common_args "$@"
case "${PM_ARGS[0]:-}" in --help)
	sed -n '2,18p' "$0"
	exit 0
	;;
esac
preflight
# shellcheck source=/dev/null
[[ -f "$HERE/.pm-ids.env" ]] && source "$HERE/.pm-ids.env" || die ".pm-ids.env missing (run setup-projects.sh)"

# Boards that get the mirrored fields, and the field definitions.
SECONDARY=("${TRIAGE_NUMBER:?}" "${RESEARCH_NUMBER:?}")
PRIORITY_OPTS="Critical,High,Medium,Low"
SEVERITY_OPTS="Critical,High,Medium,Low,Info"
PHASE_OPTS="2.5c,3A,3B,3C,3D,3E,3F,3G,3H,3I,4,4+,Deferred"

project_field_names() {
	gh project field-list "$1" --owner "$OWNER" --format json 2>/dev/null |
		jq -r '[(.fields // [])[].name] | join(",")'
}
ensure_field() { # proj name opts
	local proj="$1" name="$2" opts="$3" have
	have="$(project_field_names "$proj")"
	if [[ ",$have," == *",$name,"* ]]; then
		log "  #$proj field '$name' exists, skip"
	else
		run gh project field-create "$proj" --owner "$OWNER" --name "$name" \
			--data-type SINGLE_SELECT --single-select-options "$opts"
		ok "  #$proj created field '$name'"
	fi
}

log "== ensure Priority/Severity/Phase fields on #${TRIAGE_NUMBER} and #${RESEARCH_NUMBER} =="
for p in "${SECONDARY[@]}"; do
	ensure_field "$p" Priority "$PRIORITY_OPTS"
	ensure_field "$p" Severity "$SEVERITY_OPTS"
	ensure_field "$p" Phase "$PHASE_OPTS"
done

[[ "$DRY_RUN" == "1" ]] && {
	warn "dry-run: fields would be created; population needs them to exist, so skipping the populate pass."
	exit 0
}

# ---- populate ---------------------------------------------------------------
# Source data, gathered once:
#   1. open issues  → number, priority:/severity: label values
#   2. Main (#7)    → number → Phase option NAME (authoritative, already migrated)
#   3. each board   → number → item-id, plus field-id + option-id maps
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
gh issue list -R "$SLUG" --state open --limit 300 --json number,labels >"$TMP/issues.json"
gh project item-list "$MAIN_NUMBER" --owner "$OWNER" --format json --limit 400 >"$TMP/main.json"

apply_board() {
	local proj="$1" pid fjson
	pid="$(gh project view "$proj" --owner "$OWNER" --format json --jq '.id')"
	fjson="$(gh project field-list "$proj" --owner "$OWNER" --format json)"
	gh project item-list "$proj" --owner "$OWNER" --format json --limit 400 >"$TMP/board.json"

	# Python builds the edit plan: one TSV line per (item,field) to set —
	# proj_pid<TAB>item_id<TAB>field_id<TAB>option_id<TAB>human
	PID="$pid" FJSON="$fjson" ISSUES="$TMP/issues.json" MAIN="$TMP/main.json" BOARD="$TMP/board.json" \
		python3 - <<'PY' >"$TMP/plan.tsv"
import json, os
pid   = os.environ["PID"]
flds  = json.loads(os.environ["FJSON"]).get("fields", [])
issues= json.load(open(os.environ["ISSUES"]))
main  = json.load(open(os.environ["MAIN"])).get("items", [])
board = json.load(open(os.environ["BOARD"])).get("items", [])

# field-id + option-name→id maps for this board
fid, opt = {}, {}
for f in flds:
    if f.get("name") in ("Priority","Severity","Phase"):
        fid[f["name"]] = f["id"]
        opt[f["name"]] = { o["name"]: o["id"] for o in f.get("options", []) }

# number → priority/severity label value (capitalised to match option names)
def cap(x): return x[:1].upper()+x[1:] if x else x
pr, sv = {}, {}
for it in issues:
    n = it["number"]
    for l in it["labels"]:
        nm = l["name"]
        if nm.startswith("priority:"): pr[n] = cap(nm.split(":",1)[1])
        elif nm.startswith("severity:"): sv[n] = cap(nm.split(":",1)[1])

# number → Main's Phase option NAME
ph = {}
for it in main:
    n = (it.get("content") or {}).get("number")
    if n and it.get("phase"): ph[n] = it["phase"]

# board: number → item-id
items = {}
for it in board:
    n = (it.get("content") or {}).get("number")
    if n: items[n] = it["id"]

def emit(rows, n, field, value):
    if not value: return
    oid = opt.get(field, {}).get(value)
    if not oid:  # value has no matching option (e.g. a phase the board lacks)
        return
    rows.append((pid, items[n], fid[field], oid, f"#{n} {field}={value}"))

rows=[]
for n, iid in items.items():
    emit(rows, n, "Priority", pr.get(n))
    emit(rows, n, "Severity", sv.get(n))
    emit(rows, n, "Phase",    ph.get(n))
for r in rows:
    print("\t".join(r))
PY

	local count=0
	while IFS=$'\t' read -r p_pid item_id field_id option_id human; do
		[[ -z "$item_id" ]] && continue
		run gh project item-edit --id "$item_id" --project-id "$p_pid" \
			--field-id "$field_id" --single-select-option-id "$option_id" >/dev/null
		count=$((count + 1))
	done <"$TMP/plan.tsv"
	ok "  #$proj: set $count field values from labels + Main Phase"
}

log "== populate fields on secondary boards from labels + Main Phase =="
for p in "${SECONDARY[@]}"; do apply_board "$p"; done

ok "sync-board-fields complete (dry_run=$DRY_RUN)"

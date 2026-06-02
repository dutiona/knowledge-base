#!/usr/bin/env bash
# Idempotent super-qa issue filer (round 2, 2026-06-03).
#
#   DRY=1 ./file_issues.sh [phase]     # preview, no writes (DEFAULT-SAFE: run this first)
#   ./file_issues.sh [phase]           # execute
#
# phase = labels | epics | findings | buckets | autofix | observers | retrofit | all
#
# Idempotency: issue_state.json maps manifest-key -> {number,node}. On re-run, a key
# already in state is skipped; if state is lost, we fall back to title-match against
# all open+closed super-qa issues. Safe to re-run after partial failure.
#
# Retrofit relabels prior-round issues (#181-233): READS labels first, only ADDS missing
# ones (never removes), and links each to its area epic by its area: label.
set -uo pipefail

REPO="${REPO:-dutiona/knowledge-base}"
RUN="2026-06-03"
RUNDIR="qa/super-qa/runs/$RUN"
MANIFEST="$RUNDIR/issue_manifest.json"
STATE="$RUNDIR/issue_state.json"
DRY="${DRY:-0}"
PHASE="${1:-all}"
SEVS="critical high medium low info"

[ -f "$MANIFEST" ] || {
	echo "no manifest: $MANIFEST" >&2
	exit 1
}
[ -f "$STATE" ] || echo '{}' >"$STATE"

note() { echo "$@" >&2; }
gh_dry() {
	if [ "$DRY" = 1 ]; then
		note "[DRY] gh $*"
		return 0
	fi
	gh "$@"
}

# ---- state ledger ----
st_num() { jq -r --arg k "$1" '.[$k].number // empty' "$STATE"; }
st_node() { jq -r --arg k "$1" '.[$k].node // empty' "$STATE"; }
st_set() {
	local t
	t=$(mktemp)
	jq --arg k "$1" --arg n "$2" --arg d "$3" \
		'.[$k]={number:$n,node:$d}' "$STATE" >"$t" && mv "$t" "$STATE"
}

# ---- prefetch for title-match fallback ----
PREFETCH=$(mktemp)
gh issue list --repo "$REPO" --label super-qa --state all --limit 600 \
	--json number,title >"$PREFETCH" 2>/dev/null || echo '[]' >"$PREFETCH"
title_num() { jq -r --arg t "$1" '[.[]|select(.title==$t)|.number][0] // empty' "$PREFETCH"; }
node_of() { gh issue view "$1" --repo "$REPO" --json id -q .id 2>/dev/null; }

# ---- create one issue from a manifest key (idempotent) ----
# echoes the issue number (or DRY)
mk() {
	local key="$1" cached title bodyf labels num node tn
	cached=$(st_num "$key")
	[ -n "$cached" ] && {
		echo "$cached"
		return
	}
	title=$(jq -r --arg k "$key" '.manifest[]|select(.key==$k)|.title' "$MANIFEST")
	[ -z "$title" ] && {
		note "  !! no manifest entry: $key"
		return 1
	}
	tn=$(title_num "$title")
	if [ -n "$tn" ]; then
		node=$(node_of "$tn")
		st_set "$key" "$tn" "$node"
		note "  = exists #$tn  $key"
		echo "$tn"
		return
	fi
	bodyf=$(mktemp)
	jq -r --arg k "$key" '.manifest[]|select(.key==$k)|.body' "$MANIFEST" >"$bodyf"
	mapfile -t labels < <(jq -r --arg k "$key" '.manifest[]|select(.key==$k)|.labels[]' "$MANIFEST")
	local la=()
	for l in "${labels[@]}"; do la+=(--label "$l"); done
	if [ "$DRY" = 1 ]; then
		note "  [DRY] create: $title  [${labels[*]}]"
		rm -f "$bodyf"
		echo "DRY"
		return
	fi
	local url
	url=$(gh issue create --repo "$REPO" --title "$title" --body-file "$bodyf" "${la[@]}")
	rm -f "$bodyf"
	num=$(echo "$url" | grep -oE '[0-9]+$')
	node=$(node_of "$num")
	st_set "$key" "$num" "$node"
	note "  + #$num  $key"
	echo "$num"
}

# ---- link child key under parent key (native sub-issue) ----
link() {
	local ck="$1" pk="$2" cn pn
	cn=$(st_node "$ck")
	pn=$(st_node "$pk")
	[ -z "$cn" ] || [ -z "$pn" ] && {
		note "  ~ link skip (missing node) $ck->$pk"
		return
	}
	if [ "$DRY" = 1 ]; then
		note "  [DRY] link $ck -> $pk"
		return
	fi
	gh api graphql -f query='mutation($p:ID!,$c:ID!){addSubIssue(input:{issueId:$p,subIssueId:$c}){subIssue{number}}}' \
		-f p="$pn" -f c="$cn" >/dev/null 2>&1 && note "  > linked $ck -> $pk" || note "  ~ link failed/exists $ck -> $pk"
}
# link a raw prior issue NUMBER under a parent key
link_num() {
	local cnum="$1" pk="$2" cn pn
	cn=$(node_of "$cnum")
	pn=$(st_node "$pk")
	[ -z "$cn" ] || [ -z "$pn" ] && {
		note "  ~ link_num skip $cnum->$pk"
		return
	}
	if [ "$DRY" = 1 ]; then
		note "  [DRY] link #$cnum -> $pk"
		return
	fi
	gh api graphql -f query='mutation($p:ID!,$c:ID!){addSubIssue(input:{issueId:$p,subIssueId:$c}){subIssue{number}}}' \
		-f p="$pn" -f c="$cn" >/dev/null 2>&1 && note "  > linked #$cnum -> $pk" || note "  ~ link #$cnum failed/exists"
}

keys_of_kind() { jq -r --arg k "$1" '.manifest[]|select(.kind==$k)|.key' "$MANIFEST"; }

phase_labels() {
	note "== labels =="
	# gather every label used in the manifest + ensure taxonomy exists
	local labels
	labels=$(jq -r '.manifest[].labels[]' "$MANIFEST" | sort -u)
	while read -r l; do
		[ -z "$l" ] && continue
		local color="ededed" desc="super-qa"
		case "$l" in
		severity:critical) color="b60205" ;; severity:high) color="d93f0b" ;;
		severity:medium) color="fbca04" ;; severity:low) color="0e8a16" ;; severity:info) color="c5def5" ;;
		type:epic) color="3e4b9e" ;; super-qa) color="5319e7" ;;
		super-qa:auto-fix) color="1d76db" ;; super-qa:security-fallback) color="e99695" ;;
		esac
		gh_dry label create "$l" --repo "$REPO" --color "$color" --description "$desc" --force >/dev/null 2>&1 &&
			note "  label $l" || note "  label $l (exists)"
	done <<<"$labels"
}

phase_epics() {
	note "== epics =="
	mk "epic:master" >/dev/null
	mk "epic:autofix" >/dev/null
	for k in $(keys_of_kind area-epic); do mk "$k" >/dev/null; done
	# link area + autofix epics under master
	link "epic:autofix" "epic:master"
	for k in $(keys_of_kind area-epic); do link "$k" "epic:master"; done
}

phase_findings() {
	note "== findings (individual) =="
	for k in $(keys_of_kind finding); do
		mk "$k" >/dev/null
		local p
		p=$(jq -r --arg k "$k" '.manifest[]|select(.key==$k)|.parent' "$MANIFEST")
		link "$k" "$p"
	done
}

phase_buckets() {
	note "== low/info buckets =="
	for k in $(keys_of_kind li-group); do
		mk "$k" >/dev/null
		local p
		p=$(jq -r --arg k "$k" '.manifest[]|select(.key==$k)|.parent' "$MANIFEST")
		link "$k" "$p"
	done
}

phase_autofix() {
	note "== autofix buckets =="
	for k in $(keys_of_kind autofix); do
		mk "$k" >/dev/null
		link "$k" "epic:autofix"
	done
}

phase_observers() {
	note "== observers (create + patch #-lists) =="
	for k in $(keys_of_kind observer); do
		mk "$k" >/dev/null
		link "$k" "epic:master"
	done
	# patch each observer body with the #-list of its severity
	for sev in $SEVS; do
		local okey="observer:$sev" onum
		onum=$(st_num "$okey")
		[ -z "$onum" ] && continue
		# high/medium -> individual findings of that sev; low/info -> li-group buckets
		local body listfile
		listfile=$(mktemp)
		{
			echo "> Index of non-autofixable **$sev** findings. Auto-generated by file_issues.sh."
			echo
			echo "## Issues"
			if [ "$sev" = low ] || [ "$sev" = info ]; then
				for k in $(keys_of_kind li-group); do
					local n
					n=$(st_num "$k")
					[ -n "$n" ] && echo "- #$n — $(jq -r --arg k "$k" '.manifest[]|select(.key==$k)|.title' "$MANIFEST")"
				done
			else
				for k in $(jq -r --arg s "$sev" '.manifest[]|select(.kind=="finding" and .severity==$s)|.key' "$MANIFEST"); do
					local n
					n=$(st_num "$k")
					[ -n "$n" ] && echo "- #$n — $(jq -r --arg k "$k" '.manifest[]|select(.key==$k)|.title' "$MANIFEST" | sed 's/^\[super-qa\] [a-z]*: //')"
				done
			fi
			echo
			echo "<!-- super-qa-key: $okey -->"
		} >"$listfile"
		if [ "$DRY" = 1 ]; then note "  [DRY] patch observer #$onum ($sev): $(grep -c '^- #' "$listfile") refs"; else
			gh issue edit "$onum" --repo "$REPO" --body-file "$listfile" >/dev/null && note "  patched observer #$onum ($sev)"
		fi
		rm -f "$listfile"
	done
}

phase_retrofit() {
	note "== retrofit prior-round issues (relabel + link) =="
	# 1) explicit dupes from manifest.links_existing -> link old issue under area epic
	while read -r entry; do
		local onum akey
		onum=$(echo "$entry" | jq -r '.k')
		akey=$(echo "$entry" | jq -r '.v')
		link_num "$onum" "$akey"
	done < <(jq -c '.links_existing | to_entries[] | {k:.key,v:.value}' "$MANIFEST")
	# 2) all OPEN super-qa issues NOT created this run: ensure labels + link to area epic.
	#    Scope to --state open: closed/done issues are not backlog to reconcile, and the
	#    pre-taxonomy closed batch (#180-216, #234-245) lacks severity/area on purpose.
	local created openlist
	created=$(jq -r '[.[].number]|join(",")' "$STATE")
	openlist=$(gh issue list --repo "$REPO" --label super-qa --state open --limit 600 \
		--json number,title,labels 2>/dev/null || echo '[]')
	local noarea=()
	for onum in $(echo "$openlist" | jq -r '.[].number'); do
		# skip issues we created this round
		echo ",$created," | grep -q ",$onum," && continue
		local info labels title sev area
		info=$(echo "$openlist" | jq -c --argjson n "$onum" '.[]|select(.number==$n)')
		title=$(echo "$info" | jq -r '.title')
		labels=$(echo "$info" | jq -r '.labels[].name')
		# derive severity from title if no severity: label
		if ! grep -q '^severity:' <<<"$labels"; then
			sev=$(echo "$title" | grep -oiE '\b(critical|high|medium|low|info)\b' | head -1 | tr A-Z a-z)
			[ -n "$sev" ] && {
				gh_dry issue edit "$onum" --repo "$REPO" --add-label "severity:$sev" >/dev/null 2>&1
				note "  #$onum +severity:$sev"
			}
		fi
		grep -q '^super-qa$' <<<"$labels" || { gh_dry issue edit "$onum" --repo "$REPO" --add-label "super-qa" >/dev/null 2>&1; }
		# link to its area epic if it has an area: label; else record for manual triage
		area=$(echo "$labels" | grep -oE '^area:[a-z]+' | head -1 | cut -d: -f2)
		if [ -n "$area" ] && [ -n "$(st_num "epic:area:$area")" ]; then
			link_num "$onum" "epic:area:$area"
		else
			noarea+=("$onum")
		fi
	done
	[ ${#noarea[@]} -gt 0 ] && note "  !! open super-qa issues with no area: label (not linked, manual triage): ${noarea[*]}"
}

case "$PHASE" in
labels) phase_labels ;;
epics) phase_epics ;;
findings) phase_findings ;;
buckets) phase_buckets ;;
autofix) phase_autofix ;;
observers) phase_observers ;;
retrofit) phase_retrofit ;;
all)
	phase_labels
	phase_epics
	phase_findings
	phase_buckets
	phase_autofix
	phase_observers
	phase_retrofit
	;;
*)
	note "unknown phase: $PHASE"
	exit 1
	;;
esac
note "done ($PHASE). state: $STATE  | DRY=$DRY"

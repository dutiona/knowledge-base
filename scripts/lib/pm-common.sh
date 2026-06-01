#!/usr/bin/env bash
# pm-common.sh — shared helpers for the knowledge-base PM scripts.
# Source this from sync-labels.sh / setup-projects.sh / gen-mapping.sh /
# migrate-issues.sh. Not executable on its own.
#
# Contract:
#   OWNER / REPO         — the GitHub target.
#   DRY_RUN              — 1 when --dry-run passed; run() then echoes instead of exec.
#   ASSUME_YES           — 1 when --yes passed; confirm() then auto-accepts.
#   run "<cmd>" ...      — execute (or echo under dry-run) a mutating command.
#   log / warn / die     — stderr logging.
#   need <bin>           — assert a binary is on PATH.
#   confirm "<prompt>"   — y/N gate for destructive steps (auto-yes under ASSUME_YES).
#   label_exists <name>  — 0 if a label exists in REPO.

set -euo pipefail

OWNER="${OWNER:-dutiona}"
REPO="${REPO:-knowledge-base}"
SLUG="${OWNER}/${REPO}"

DRY_RUN="${DRY_RUN:-0}"
ASSUME_YES="${ASSUME_YES:-0}"

# ---- logging (all to stderr so stdout stays parseable) ----------------------
_c_reset=$'\033[0m'
_c_dim=$'\033[2m'
_c_yel=$'\033[33m'
_c_red=$'\033[31m'
_c_grn=$'\033[32m'
log() { printf '%s[pm]%s %s\n' "$_c_dim" "$_c_reset" "$*" >&2; }
ok() { printf '%s[pm]%s %s\n' "$_c_grn" "$_c_reset" "$*" >&2; }
warn() { printf '%s[pm:warn]%s %s\n' "$_c_yel" "$_c_reset" "$*" >&2; }
die() {
	printf '%s[pm:err]%s %s\n' "$_c_red" "$_c_reset" "$*" >&2
	exit 1
}

# ---- dry-run-aware command runner -------------------------------------------
# Usage: run gh label create "type:bug" --color d73a4a ...
# Under DRY_RUN it prints the command (quoted) and does nothing.
run() {
	if [[ "$DRY_RUN" == "1" ]]; then
		printf '%s[dry-run]%s' "$_c_yel" "$_c_reset" >&2
		printf ' %q' "$@" >&2
		printf '\n' >&2
		return 0
	fi
	"$@"
}

# ---- preflight --------------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || die "required binary not found: $1"; }

preflight() {
	need gh
	need jq
	gh auth status >/dev/null 2>&1 || die "gh not authenticated (run: gh auth login)"
	# token scope sanity — project ops need the 'project' scope
	local scopes
	scopes="$(gh auth status 2>&1 | grep -i 'Token scopes' || true)"
	case "$scopes" in
	*project*) : ;;
	*) warn "gh token may lack 'project' scope — project mutations could 403. ($scopes)" ;;
	esac
	log "target: $SLUG  dry_run=$DRY_RUN  assume_yes=$ASSUME_YES"
}

# ---- destructive gate -------------------------------------------------------
confirm() {
	local prompt="${1:-Proceed?}"
	if [[ "$ASSUME_YES" == "1" || "$DRY_RUN" == "1" ]]; then
		log "confirm (auto): $prompt"
		return 0
	fi
	local reply
	printf '%s[confirm]%s %s [y/N] ' "$_c_yel" "$_c_reset" "$prompt" >&2
	read -r reply || true
	case "$reply" in
	[yY] | [yY][eE][sS]) return 0 ;;
	*)
		warn "skipped: $prompt"
		return 1
		;;
	esac
}

# ---- helpers ----------------------------------------------------------------
# Cached label snapshot — one API call, reused across many label_exists() checks.
# sync-labels.sh checks ~15 labels in a burst; per-check API calls risk a
# transient blip silently returning "absent". Fetch once, refresh after mutation.
PM_LABELS_CACHE=""
refresh_labels_cache() {
	PM_LABELS_CACHE="$(gh label list -R "$SLUG" --limit 300 --json name -q '.[].name' 2>/dev/null)" ||
		die "failed to list labels for $SLUG (gh error)"
	# A repo always has >=1 label here; empty almost certainly means a gh failure.
	[[ -n "$PM_LABELS_CACHE" ]] || die "empty label list for $SLUG — refusing to proceed (likely a gh/API error, not a truly empty repo)"
}

label_exists() {
	# 0 if label "$1" exists in REPO. Exact full-line match (handles spaces, e.g.
	# "good first issue"). Uses the cached snapshot; populates it on first use.
	[[ -n "$PM_LABELS_CACHE" ]] || refresh_labels_cache
	grep -qxF -- "$1" <<<"$PM_LABELS_CACHE"
}

# Standard arg parse shared by the scripts. Sets DRY_RUN/ASSUME_YES.
# Call: parse_common_args "$@"; then handle any leftover positionals yourself
# (they are re-exported in PM_ARGS).
parse_common_args() {
	PM_ARGS=()
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--dry-run) DRY_RUN=1 ;;
		--yes | -y) ASSUME_YES=1 ;;
		-h | --help) PM_ARGS+=("--help") ;;
		*) PM_ARGS+=("$1") ;;
		esac
		shift
	done
}

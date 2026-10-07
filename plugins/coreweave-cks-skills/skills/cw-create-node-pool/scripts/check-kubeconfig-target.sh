#!/usr/bin/env bash
# Read local kubeconfig metadata only; never print credentials or contact a cluster.
# Usage: bash check-kubeconfig-target.sh [--current] FILE CONTEXT EXPECTED_SERVER
# --current additionally checks the file's current-context (Terraform's target).
set -euo pipefail
current=0
if [[ "${1:-}" == --current ]]; then current=1; shift; fi
[[ $# == 3 ]] || { echo 'usage: check-kubeconfig-target.sh [--current] FILE CONTEXT EXPECTED_SERVER' >&2; exit 1; }
kcfg="$1"; context="$2"; expected="$3"
[[ -n "$kcfg" && -f "$kcfg" && -n "$context" && -n "$expected" ]] || {
  echo 'Target check requires an existing kubeconfig, context, and resolved API server.' >&2; exit 1;
}
# Terraform may return either a hostname or an HTTPS URL. Never invent a host
# from a cluster name, and never accept another transport or embedded credentials.
case "$expected" in
  https://*) ;;
  *://*) echo 'Expected API server must use HTTPS.' >&2; exit 1 ;;
  *) expected="https://$expected" ;;
esac
expected="${expected%/}"
[[ "$expected" != 'https://' && "$expected" != *@* && "$expected" != *'?'* && "$expected" != *'#'* && "$expected" != *[[:space:]]* ]] || {
  echo 'Expected API server is invalid.' >&2; exit 1;
}
if (( current )); then
  [[ "$(kubectl --kubeconfig "$kcfg" config current-context)" == "$context" ]] || {
    echo 'Kubeconfig current-context does not match the selected context.' >&2; exit 1;
  }
fi
selected="$(kubectl --kubeconfig "$kcfg" --context "$context" config view --minify -o jsonpath='{.contexts[0].name}')"
[[ "$selected" == "$context" ]] || { echo 'Selected context is missing or unreadable.' >&2; exit 1; }
server="$(kubectl --kubeconfig "$kcfg" --context "$context" config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
[[ "${server%/}" == "$expected" ]] || {
  echo 'Selected context points at a different API server; stop before acting or reporting evidence.' >&2; exit 1;
}
printf 'Verified context %s at %s\n' "$context" "$expected"

#!/usr/bin/env bash
# CLI end-to-end test (release workflow, D-092): the `trustbook` command inside the image, against the
# running app with the mock chat model. Create a space, paste a source and wait for it, ask an
# answerable question (cited JSON, exit 0) and an unanswerable one (refused, exit 2).
# usage: cli_e2e.sh [CONTAINER]   (needs docker and jq)
set -uo pipefail
container="${1:-trustbook-e2e}"
space="CLI e2e"
fail() { echo "CLI e2e FAILED: $*" >&2; exit 1; }
tb() { docker exec -i "$container" trustbook "$@"; }

out=$(tb --version) || fail "--version"
echo "$out"

out=$(tb spaces create "$space" --json) || fail "spaces create: $out"
jq -e '.schema == "trustbook.space/1"' <<<"$out" >/dev/null || fail "spaces create: $out"

text="The Gull Island lighthouse was built in 1874 by the harbour board. Its lamp was converted to electricity in 1952."
out=$(printf '%s\n' "$text" | tb sources add paste - --title "Gull Island" --space "$space" --wait --timeout 300 --json) \
  || fail "sources add paste: $out"
jq -e '.schema == "trustbook.sources/1" and .sources[0].status == "ready"' <<<"$out" >/dev/null \
  || fail "sources add paste: $out"

out=$(tb ask "When was the Gull Island lighthouse built?" --space "$space" --json); code=$?
[ "$code" -eq 0 ] || fail "ask (answerable) exited $code: $out"
jq -e '.schema == "trustbook.ask/1"
       and (.verdict == "verified" or .verdict == "partially_verified")
       and (.citations | length) > 0
       and (.citations[0].text | contains("1874"))
       and (.sentences[0].citations | length) > 0' <<<"$out" >/dev/null \
  || fail "ask (answerable): $out"

out=$(tb ask "What is the capital of Mongolia?" --space "$space" --json); code=$?
[ "$code" -eq 2 ] || fail "ask (unanswerable) exited $code, expected 2: $out"
jq -e '.verdict == "refused" and .refusal.reason != null and (.citations | length) == 0' <<<"$out" >/dev/null \
  || fail "ask (unanswerable): $out"

out=$(tb spaces delete "$space" --yes --json) || fail "spaces delete: $out"
echo "CLI e2e: PASS"

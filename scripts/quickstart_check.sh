#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
DEPONE_ROOT="${WITNESSD_DEPONE_ROOT:-}"
if [ -z "$DEPONE_ROOT" ]; then
  DEPONE_ROOT="$(cd "$ROOT/../depone" && pwd)"
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

repo="$tmp/repo"
home="$tmp/home"
mkdir -p "$repo"
git -C "$repo" init -q
git -C "$repo" config user.email "quickstart@example.invalid"
git -C "$repo" config user.name "witnessd quickstart"
printf '%s\n' "seed" > "$repo/README.txt"
git -C "$repo" add -A
git -C "$repo" commit -qm "seed"

cd "$ROOT"
"$PYTHON_BIN" -m witnessd init --home "$home" --depone-root "$DEPONE_ROOT" >/dev/null
run_json="$("$PYTHON_BIN" -m witnessd run "quickstart: write two independent files" --repo "$repo" --home "$home")"
run_dir="$("$PYTHON_BIN" -c 'import json,sys; print(json.loads(sys.argv[1])["run_dir"])' "$run_json")"
rm -f "$run_dir/team-ledger-verdict.json"
verify_json="$("$PYTHON_BIN" -m witnessd verify "$run_dir" --home "$home")"
"$PYTHON_BIN" -c 'import json,sys; p=json.loads(sys.argv[1]); assert p["decision"] == "pass", p' "$verify_json"
printf '%s\n' "quickstart_check: pass"

#!/bin/bash
# Syntax-check ES modules. `node --check` parses .js as CommonJS and rejects
# `export`, so each file is checked through a .mjs copy.
fail=0
scratch=$(mktemp -d "${TMPDIR:-/tmp}/ficelo-jscheck.XXXXXX") || exit 1
trap 'rm -rf "$scratch"' EXIT
for f in "$@"; do
  if ! cp "$f" "$scratch/source.mjs"; then
    fail=1
    continue
  fi
  if ! node --check "$scratch/source.mjs" 2>"$scratch/error"; then
    echo "FAIL $f"; sed -n '2,6p' "$scratch/error"; fail=1
  fi
done
[ $fail -eq 0 ] && echo "syntax ok: $# file(s)"
exit $fail

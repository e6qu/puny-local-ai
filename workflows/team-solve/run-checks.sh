#!/usr/bin/env bash
# The goal check of team-solve, run in the problem folder: the samples
# (tests/N.in through run.sh, compared with tests/N.out) when there are any,
# then the folder's own check.sh when there is one. Exits 0 only when every
# check passes. Adapted from Memdoor's team-solve (Apache-2.0); see NOTICE
# and CHANGES.md.
set -u
shopt -s nullglob
inputs=(tests/*.in)
if [ "${#inputs[@]}" -eq 0 ] && [ ! -f check.sh ]; then
    printf 'FAIL: no goal to check: add tests/N.in with tests/N.out, or check.sh\n'
    exit 1
fi
failures=0
if [ "${#inputs[@]}" -gt 0 ]; then
    if [ ! -f run.sh ]; then
        printf 'FAIL: there is no run.sh, the command that runs the program on a sample\n'
        exit 1
    fi
    actual=$(mktemp) || exit 1
    trap 'rm -f "$actual"' EXIT
    for input in "${inputs[@]}"; do
        expected="${input%.in}.out"
        if [ ! -f "$expected" ]; then
            printf 'FAIL %s: missing %s\n' "$input" "$expected"
            failures=$((failures + 1))
        elif ! bash run.sh < "$input" > "$actual"; then
            printf 'FAIL %s: run.sh exited unsuccessfully\n' "$input"
            failures=$((failures + 1))
        elif diff -u "$expected" "$actual"; then
            printf 'PASS %s\n' "$input"
        else
            printf 'FAIL %s: output differs\n' "$input"
            failures=$((failures + 1))
        fi
    done
    printf 'Samples: %s; failures: %s\n' "${#inputs[@]}" "$failures"
fi
if [ -f check.sh ]; then
    if bash check.sh; then
        printf 'PASS check.sh\n'
    else
        printf 'FAIL check.sh\n'
        failures=$((failures + 1))
    fi
fi
printf 'Checks failed: %s\n' "$failures"
[ "$failures" -eq 0 ]

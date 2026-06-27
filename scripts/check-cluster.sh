#!/usr/bin/env bash
set -euo pipefail

run() {
  printf '\n+'
  printf ' %q' "$@"
  printf '\n'
  "$@"
}

run kubectl config current-context
run kubectl get nodes -o wide
run kubectl get ns


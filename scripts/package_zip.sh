#!/usr/bin/env bash

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_name="$(basename "$repo_dir")"
parent_dir="$(dirname "$repo_dir")"
archive_path="$parent_dir/${repo_name}.zip"

cd "$parent_dir"

zip -r "$archive_path" "$repo_name" \
  -x "$repo_name/.git/*" \
  -x "$repo_name/.venv/*" \
  -x "$repo_name/venv*" \
  -x "$repo_name/venv*/*" \
  -x "$repo_name/__pycache__/*" \
  -x "$repo_name/*.pyc"

printf 'Created %s\n' "$archive_path"

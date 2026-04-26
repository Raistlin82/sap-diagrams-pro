#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
#
# bootstrap-cache.sh — clone SAP source-of-truth repos to ~/.cache/sap-diagrams-pro/
# so the plugin can resolve service icons and reference architectures without
# bundling SAP assets (license-clean separation).
#
# Idempotent: safe to re-run. Pass --refresh to force a re-pull.
# Pass --shallow=false to disable shallow clones (default is depth=1).

set -euo pipefail

CACHE_DIR="${SAP_DIAGRAMS_CACHE:-$HOME/.cache/sap-diagrams-pro}"
BTP_REPO="https://github.com/SAP/btp-solution-diagrams.git"
ARCH_REPO="https://github.com/SAP/architecture-center.git"
BTP_DIR="$CACHE_DIR/btp-solution-diagrams"
ARCH_DIR="$CACHE_DIR/architecture-center"

REFRESH=0
SHALLOW=1

for arg in "$@"; do
  case "$arg" in
    --refresh) REFRESH=1 ;;
    --shallow=false) SHALLOW=0 ;;
    -h|--help)
      cat <<EOF
Usage: bootstrap-cache.sh [--refresh] [--shallow=false]

Clones the official SAP repos into:
  $CACHE_DIR/btp-solution-diagrams
  $CACHE_DIR/architecture-center

Override cache location via SAP_DIAGRAMS_CACHE env var.

Options:
  --refresh         Force git pull --rebase even if repo already exists.
  --shallow=false   Full history clone (default: --depth=1).
  -h, --help        Show this help.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git is required but not found in PATH" >&2
  exit 1
fi

mkdir -p "$CACHE_DIR"

clone_or_pull() {
  local url="$1"
  local dir="$2"
  local name
  name="$(basename "$dir")"

  if [[ -d "$dir/.git" ]]; then
    if [[ $REFRESH -eq 1 ]]; then
      echo "↻ Refreshing $name..."
      git -C "$dir" fetch --all --prune --quiet
      git -C "$dir" pull --rebase --quiet || {
        echo "WARN: pull failed for $name — keeping existing snapshot" >&2
        return 0
      }
    else
      echo "✓ $name already cached at $dir (use --refresh to update)"
    fi
  else
    echo "↓ Cloning $name to $dir..."
    if [[ $SHALLOW -eq 1 ]]; then
      git clone --depth=1 --quiet "$url" "$dir"
    else
      git clone --quiet "$url" "$dir"
    fi
  fi

  local sha
  sha="$(git -C "$dir" rev-parse --short HEAD)"
  echo "  └── HEAD: $sha"
}

clone_or_pull "$BTP_REPO" "$BTP_DIR"
clone_or_pull "$ARCH_REPO" "$ARCH_DIR"

echo ""
echo "✅ SAP source-of-truth cached at: $CACHE_DIR"
echo ""
echo "Next: run 'python3 scripts/build-shape-index.py' to build the shape index."

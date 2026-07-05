#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
#
# Assemble the Claude Desktop / claude.ai Agent Skill bundle from the single
# source-of-truth engine (scripts/ + assets/) plus the Desktop-flavoured SKILL.md.
# Output (gitignored): dist/claude-desktop-skill/sap-diagram-generate/ + a .zip
# ready to upload to claude.ai (Settings -> Capabilities -> Skills) or via the
# Claude API Skills endpoint. Re-run after any change to the engine or assets.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SKILL_NAME="sap-diagram-generate"
OUT="$ROOT/dist/claude-desktop-skill"
STAGE="$OUT/$SKILL_NAME"

rm -rf "$STAGE"
mkdir -p "$STAGE/scripts" "$STAGE/assets"

cp "$HERE/SKILL.md" "$STAGE/SKILL.md"
# NOTE (Task 6): _zone_layout.py was replaced by _skeleton_layout.py (which
# imports _molecules.py). The full Desktop-bundle refresh (contract + brand pack
# + router assets) is Task 18; this copy loop is kept minimally correct here so
# the build no longer references the deleted file.
for f in generate-drawio.py _skeleton_layout.py _molecules.py validate-drawio.py check-composition.py; do
  cp "$ROOT/scripts/$f" "$STAGE/scripts/$f"
done
for f in shape-index.json canonical-pills.json; do
  cp "$ROOT/assets/$f" "$STAGE/assets/$f"
done

ZIP="$OUT/$SKILL_NAME.zip"
rm -f "$ZIP"
if command -v zip >/dev/null 2>&1; then
  ( cd "$OUT" && zip -qr "$SKILL_NAME.zip" "$SKILL_NAME" )
  ZIP_MSG="$ZIP"
else
  ZIP_MSG="(zip not installed — upload the folder, or zip it manually)"
fi

echo "Built Agent Skill bundle:"
echo "  folder: $STAGE"
echo "  zip:    $ZIP_MSG"
echo "  size:   $(du -sh "$STAGE" | cut -f1)  (engine + assets bundled, self-contained)"

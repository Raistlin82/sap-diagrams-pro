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
mkdir -p "$STAGE/scripts" "$STAGE/assets" "$STAGE/references"

cp "$HERE/SKILL.md" "$STAGE/SKILL.md"

# Engine — the perfect-diagrams pipeline (IR v2 -> skeleton layout -> channel
# router -> molecules -> geometric gate + visual-rubric loop -> drawio / pure
# render). Public entry points + the private modules they import at runtime.
# NOTE: _zone_layout.py was deleted (Task 6, superseded by _skeleton_layout.py);
# do NOT reference it. _drawio_io.py is a transitive dep of _pure_render.py.
SCRIPTS=(
  # entry points
  generate-drawio.py        # IR v2 -> .drawio
  validate-ir.py            # IR v2 pre-render gate
  validate-drawio.py        # emitted-XML validator
  check-composition.py      # geometric composition gate
  apply-rubric-patches.py   # visual-rubric patch-op consumer
  render-preview.py         # pure-Python PNG preview (no draw.io app)
  # private modules (path-imported by the entry points above)
  _skeleton_layout.py       # slot layout + flow ordering
  _channel_router.py        # deterministic edge router
  _molecules.py             # style-contract-driven molecule emission
  _geom_checks.py           # geometry kernel (router + gate)
  _pure_render.py           # sandbox PNG renderer
  _drawio_io.py             # drawio page (de)serialisation (used by _pure_render)
)
for f in "${SCRIPTS[@]}"; do
  cp "$ROOT/scripts/$f" "$STAGE/scripts/$f"
done

# Assets — single files.
for f in shape-index.json canonical-pills.json style-contract.json; do
  cp "$ROOT/assets/$f" "$STAGE/assets/$f"
done

# Assets — directories. Copy the PUBLIC brand pack ONLY; assets/brand-pack.local
# (gitignored trademarks / customer logos) MUST NEVER enter the bundle, so the
# copies below are explicit and never glob 'brand-pack*'.
cp -R "$ROOT/assets/brand-pack" "$STAGE/assets/brand-pack"   # public brand chips
cp -R "$ROOT/assets/fonts"      "$STAGE/assets/fonts"        # bundled Arimo (SIL OFL-1.1)

# Icon atlas — the source has ~360 loose PNGs under icon-atlas/icons/, which blows
# past claude.ai's 200-file Skills upload limit. Pack every referenced PNG as
# base64 into a single index.json ``embedded`` map (one file, no icons/ dir);
# _pure_render.load_icon reads embedded pixels when the loose file is absent.
mkdir -p "$STAGE/assets/icon-atlas"
python3 - "$ROOT/assets/icon-atlas" "$STAGE/assets/icon-atlas/index.json" <<'PY'
import sys, json, base64, pathlib
src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
idx = json.loads((src / "index.json").read_text(encoding="utf-8"))
embedded = {}
for rel in sorted(set(idx.get("by_sha1", {}).values())):
    f = src / rel
    if f.exists():
        embedded[rel] = base64.b64encode(f.read_bytes()).decode("ascii")
idx["embedded"] = embedded
out.write_text(json.dumps(idx), encoding="utf-8")
print(f"  icon-atlas: packed {len(embedded)} PNGs into index.json (embedded base64)")
PY

# Visual-rubric reference (the ~25 binary checks the rubric loop applies).
cp "$ROOT/skills/sap-diagram-generate/references/visual-rubric.md" \
   "$STAGE/references/visual-rubric.md"

# Safety net: fail loudly if any brand-pack.local artefact slipped into the stage.
if find "$STAGE" -path '*brand-pack.local*' -print -quit | grep -q .; then
  echo "ERROR: brand-pack.local leaked into the bundle — aborting." >&2
  exit 1
fi

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

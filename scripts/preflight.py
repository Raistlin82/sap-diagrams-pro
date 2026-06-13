#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Gabriele Capparelli
# SPDX-License-Identifier: Apache-2.0
"""
preflight.py — dependency gate for sap-diagrams-pro.

Before generating any diagram, the plugin must be sure the *content* sources it
relies on are present: the SAP-domain reference skills (secondsky/sap-skills and
sap-pce-expert) and the SAP documentation MCP servers (marianfoo/mcp-sap-docs et
al.). Without them the generator cannot ground the component inventory in
authoritative SAP sources (canonical service names, BTP-service vs SaaS-product
classification, best-practice completeness) and would be guessing.

This script reports what is installed/configured vs missing, and prints the exact
install command for each gap. It is intentionally read-only and side-effect free.

Detection scope:
  • Reference skills — scanned on the local filesystem (plugin marketplaces +
    cache dirs). Deterministic.
  • MCP servers — read from Claude config files (``mcpServers`` keys). This tells
    us a server is *configured*; the running agent must still confirm the tools
    are callable in-session (a config-only check cannot prove reachability).
  • Tooling — python3 (implicit) and draw.io (used by render-preview.py).

Usage:
    python3 preflight.py                 # human-readable report
    python3 preflight.py --json          # machine-readable
    python3 preflight.py --strict        # exit 1 if any REQUIRED item is missing
    python3 preflight.py --need cap,ai   # also require skills for these concerns

Exit codes:
    0  — all REQUIRED present (or --strict not set)
    1  — at least one REQUIRED missing and --strict set
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HOME = Path(os.path.expanduser("~"))

# ─────────────────────────────────────────────────────────────────────────────
# Reference skills relevant to SAP solution diagrams.
#   level: "required"  → always needed to ground content
#          "recommended" → consult when the matching concern appears
#   concern: short tag the SKILL can match against the parsed description, so a
#            CAP-only diagram doesn't nag about Datasphere.
# ─────────────────────────────────────────────────────────────────────────────
REFERENCE_SKILLS: list[dict] = [
    {"name": "sap-btp-best-practices",          "level": "required",    "concern": "always",        "why": "operational completeness (logging, audit, alerting, identity)"},
    {"name": "sap-btp-connectivity",            "level": "recommended", "concern": "onprem",        "why": "Cloud Connector / destinations / on-prem access"},
    {"name": "sap-btp-cloud-identity-services", "level": "recommended", "concern": "identity",      "why": "IAS / XSUAA trust, authentication & authorization flows"},
    {"name": "sap-btp-cloud-logging",           "level": "recommended", "concern": "observability", "why": "observability / Cloud Logging"},
    {"name": "sap-btp-integration-suite",       "level": "recommended", "concern": "integration",   "why": "iFlows, API Management, EDI/B2B"},
    {"name": "sap-btp-build-work-zone-advanced","level": "recommended", "concern": "portal",        "why": "Build Work Zone / launchpad / portal"},
    {"name": "sap-btp-job-scheduling",          "level": "recommended", "concern": "scheduling",    "why": "Job Scheduling Service"},
    {"name": "sap-cap-capire",                  "level": "recommended", "concern": "cap",           "why": "CAP application architecture"},
    {"name": "sap-fiori-tools",                 "level": "recommended", "concern": "fiori",         "why": "Fiori / UI5 front-ends"},
    {"name": "sap-ai-core",                     "level": "recommended", "concern": "ai",            "why": "AI Core / GenAI Hub / DOX"},
    {"name": "sap-datasphere",                  "level": "recommended", "concern": "data",          "why": "Datasphere / analytics / data flows"},
    {"name": "sap-api-style",                   "level": "recommended", "concern": "api",           "why": "clean-core, released C1 APIs"},
    {"name": "sap-btp-developer-guide",         "level": "recommended", "concern": "always",        "why": "general BTP development guidance"},
    {"name": "sap-pce-expert",                  "level": "recommended", "concern": "pce",           "why": "RISE / Private Cloud Edition, Private Link"},
]

# MCP servers used for content grounding. `key` is the name expected under the
# config `mcpServers` map.
MCP_SERVERS: list[dict] = [
    {"key": "sap-docs",        "level": "required",    "why": "SAP Discovery Center + docs grounding (marianfoo/mcp-sap-docs)",
     "install": "claude mcp add sap-docs -- npx -y @marianfoo/mcp-sap-docs   # https://github.com/marianfoo/mcp-sap-docs"},
    {"key": "sap-note-search", "level": "recommended", "why": "SAP Notes lookup",
     "install": "see your SAP Notes MCP provider"},
    {"key": "sap-cds-mcp",     "level": "recommended", "why": "CDS / CAP model assistance",
     "install": "see the sap-cds MCP provider"},
    {"key": "sap-fiori-mcp",   "level": "recommended", "why": "Fiori tooling assistance",
     "install": "see the sap-fiori MCP provider"},
]

SKILLS_INSTALL_CMD = "npx skills add secondsky/sap-skills"
PCE_INSTALL_HINT = "install the sap-pce-expert plugin (separate marketplace)"


def _plugin_roots() -> list[Path]:
    """Candidate directories where Claude Code stores installed plugins/skills."""
    base = HOME / ".claude" / "plugins"
    return [
        base / "marketplaces" / "sap-skills" / "plugins",
        base / "cache" / "sap-skills",
        base / "marketplaces" / "sap-pce-expert" / "skills",
        base / "cache" / "sap-pce-expert",
        base / "marketplaces",  # generic fallback (skill may live one level down)
        base / "cache",
    ]


def detect_installed_skills() -> set[str]:
    """Return the set of skill folder names found anywhere under the plugin roots."""
    found: set[str] = set()
    for root in _plugin_roots():
        if not root.is_dir():
            continue
        try:
            for child in root.iterdir():
                if child.is_dir():
                    found.add(child.name)
                    # one level deeper (marketplace/<mp>/plugins/<skill>)
                    sub = child / "plugins"
                    if sub.is_dir():
                        for g in sub.iterdir():
                            if g.is_dir():
                                found.add(g.name)
        except OSError:
            continue
    return found


def _config_paths() -> list[Path]:
    cwd = Path.cwd()
    return [
        HOME / ".claude.json",
        HOME / ".claude" / "settings.json",
        HOME / ".claude" / "settings.local.json",
        cwd / ".mcp.json",
        cwd / ".claude" / "settings.json",
        cwd / ".claude" / "settings.local.json",
    ]


def detect_configured_mcp() -> set[str]:
    """Collect every key seen under any ``mcpServers`` object in the configs."""
    names: set[str] = set()

    def walk(obj) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "mcpServers" and isinstance(v, dict):
                    names.update(v.keys())
                else:
                    walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    for p in _config_paths():
        if not p.is_file():
            continue
        try:
            walk(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return names


def detect_drawio() -> str | None:
    """Return a usable draw.io launcher path, or None."""
    mac = "/Applications/draw.io.app/Contents/MacOS/draw.io"
    if Path(mac).exists():
        return mac
    for name in ("drawio", "draw.io"):
        which = shutil.which(name)
        if which:
            return which
    return None


def run(need: list[str]) -> dict:
    installed_skills = detect_installed_skills()
    configured_mcp = detect_configured_mcp()
    need_set = {n.strip().lower() for n in need if n.strip()}

    skills_report = []
    for s in REFERENCE_SKILLS:
        present = s["name"] in installed_skills
        # Promote a recommended skill to "needed-now" if its concern was requested.
        needed_now = s["level"] == "required" or s["concern"] in need_set
        skills_report.append({**s, "present": present, "needed_now": needed_now})

    mcp_report = []
    for m in MCP_SERVERS:
        mcp_report.append({**m, "present": m["key"] in configured_mcp})

    drawio = detect_drawio()
    tooling = {
        "python3": sys.version.split()[0],
        "drawio": drawio,
    }

    required_missing = (
        [s for s in skills_report if s["level"] == "required" and not s["present"]]
        + [m for m in mcp_report if m["level"] == "required" and not m["present"]]
    )
    return {
        "skills": skills_report,
        "mcp": mcp_report,
        "tooling": tooling,
        "required_missing": [x.get("name") or x.get("key") for x in required_missing],
        "ready": not required_missing,
    }


def render_text(rep: dict) -> str:
    def mark(ok: bool) -> str:
        return "✅" if ok else "❌"

    out = ["SAP Diagrams Pro — Preflight", ""]

    out.append("Reference skills (secondsky/sap-skills · sap-pce-expert)")
    any_skill_missing = False
    for s in rep["skills"]:
        tag = "required" if s["level"] == "required" else (
            "needed-now" if s["needed_now"] else "recommended")
        if not s["present"] and (s["level"] == "required" or s["needed_now"]):
            any_skill_missing = True
        out.append(f"  {mark(s['present'])} {s['name']:34} ({tag:11}) {s['why']}")
    if any_skill_missing:
        out.append(f"  → install: {SKILLS_INSTALL_CMD}   ({PCE_INSTALL_HINT} for sap-pce-expert)")
    out.append("")

    out.append("MCP servers (content grounding)")
    for m in rep["mcp"]:
        out.append(f"  {mark(m['present'])} {m['key']:16} ({m['level']:11}) {m['why']}")
        if not m["present"]:
            out.append(f"      → {m['install']}")
    out.append("  note: 'configured' ≠ reachable — the agent confirms the MCP tools are callable in-session.")
    out.append("")

    out.append("Tooling")
    out.append(f"  {mark(True)} python3 {rep['tooling']['python3']}")
    drawio = rep["tooling"]["drawio"]
    out.append(f"  {mark(bool(drawio))} draw.io (PNG render/preview) "
               + (drawio or "— not found; visual verification loop will be skipped"))
    out.append("")

    if rep["ready"]:
        out.append("Summary: READY ✓ — required skills + MCP present. Safe to ground content and generate.")
    else:
        miss = ", ".join(rep["required_missing"])
        out.append(f"Summary: NOT READY ✗ — missing REQUIRED: {miss}. Install above, then re-run.")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Preflight dependency gate for sap-diagrams-pro.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any REQUIRED item is missing")
    ap.add_argument("--need", default="", help="comma-separated concern tags to promote (e.g. cap,ai,onprem,pce)")
    args = ap.parse_args(argv)

    rep = run(args.need.split(","))
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print(render_text(rep), end="")

    if args.strict and not rep["ready"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

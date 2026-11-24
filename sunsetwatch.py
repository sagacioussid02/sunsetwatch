#!/usr/bin/env python3
"""SunsetWatch POC - scan a codebase for AI model IDs and MCP server
packages, and check them against a local deprecation registry.

Proof of concept only: local, read-only, stdlib-only. See README.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# --------------------------------------------------------------------------
# Scanner
# --------------------------------------------------------------------------

# (compiled regex, provider guess). Patterns are deliberately broad: a POC
# scanner over-reports model-like strings, and the registry lookup decides
# what is known. False-positive risk is a documented dossier risk.
MODEL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bgpt-[a-z0-9][a-z0-9.\-]*"), "OpenAI"),
    (re.compile(r"\bo[134](?:-[a-z0-9][a-z0-9.\-]*)?\b"), "OpenAI"),
    (re.compile(r"\btext-embedding-[a-z0-9][a-z0-9.\-]*"), "OpenAI"),
    (re.compile(r"\bdall-e-[a-z0-9][a-z0-9.\-]*"), "OpenAI"),
    (re.compile(r"\bwhisper-[a-z0-9][a-z0-9.\-]*"), "OpenAI"),
    (re.compile(r"\bclaude-[a-z0-9][a-z0-9.\-]*"), "Anthropic"),
    (re.compile(r"\bgemini-[a-z0-9][a-z0-9.\-]*"), "Google"),
    (re.compile(r"\bmistral-[a-z0-9][a-z0-9.\-]*"), "Mistral"),
    (re.compile(r"\bllama-[a-z0-9][a-z0-9.\-]*"), "Meta"),
    (re.compile(r"\bcommand-(?:r|a|light|nightly)[a-z0-9.\-]*"), "Cohere"),
]

SCAN_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".java", ".rb",
    ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh",
}
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
    "build", ".next", "target", ".tox",
}
MAX_FILE_BYTES = 2_000_000


@dataclass
class Finding:
    model: str
    provider_guess: str
    file: str
    line: int
    snippet: str


@dataclass
class ParamFinding:
    param: str
    provider: str
    api_version: str
    file: str
    line: int
    snippet: str


@dataclass
class McpFinding:
    package: str
    file: str
    line: int
    snippet: str


def is_scannable(path: Path) -> bool:
    if path.name.startswith(".env"):
        return True
    return path.suffix.lower() in SCAN_EXTENSIONS


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if is_scannable(path):
            yield path


def scan_file(path: Path, root: Path) -> tuple[list[Finding], str]:
    """Return (model findings, raw text) for one file."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return [], ""
        raw = path.read_bytes()
    except OSError:
        return [], ""
    if b"\x00" in raw[:4096]:  # binary file, skip
        return [], ""
    text = raw.decode("utf-8", errors="ignore")
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    seen_on_line: set[tuple[int, str]] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern, provider in MODEL_PATTERNS:
            for match in pattern.finditer(line):
                model = match.group(0).rstrip("-.")
                key = (lineno, model)
                if key in seen_on_line:
                    continue
                seen_on_line.add(key)
                findings.append(
                    Finding(
                        model=model,
                        provider_guess=provider,
                        file=rel,
                        line=lineno,
                        snippet=line.strip()[:120],
                    )
                )
    return findings, text


PARAM_RE = re.compile(r"\b(temperature|top_p|top_k)\b\s*[:=]")


def scan_params(text: str, rel_file: str, registry: "Registry") -> list[ParamFinding]:
    """Flag removed Anthropic params in files that reference Anthropic/Claude."""
    lowered = text.lower()
    out: list[ParamFinding] = []
    for rule in registry.param_deprecations:
        if rule["provider"].lower() not in lowered and "claude" not in lowered:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in PARAM_RE.finditer(line):
                if match.group(1) in rule["params"]:
                    out.append(
                        ParamFinding(
                            param=match.group(1),
                            provider=rule["provider"],
                            api_version=rule["api_version"],
                            file=rel_file,
                            line=lineno,
                            snippet=line.strip()[:120],
                        )
                    )
    return out


# MCP servers appear in client configs (.mcp.json, claude_desktop_config.json,
# VS Code mcp.json, ~/.claude.json) as package names launched via npx/uvx -
# they are not project dependencies, so a config scan is the only place they
# show up. Two naming schemes: npm @modelcontextprotocol/server-* and
# PyPI mcp-server-*.
MCP_PACKAGE_RE = re.compile(
    r"@modelcontextprotocol/(?:server-[a-z0-9][a-z0-9\-]*|create-server)"
    r"|\bmcp-server-[a-z0-9][a-z0-9\-]*"
)


def scan_mcp(text: str, rel_file: str) -> list[McpFinding]:
    """Find MCP server package references in one file's text."""
    out: list[McpFinding] = []
    seen_on_line: set[tuple[int, str]] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in MCP_PACKAGE_RE.finditer(line):
            package = match.group(0)
            key = (lineno, package)
            if key in seen_on_line:
                continue
            seen_on_line.add(key)
            out.append(
                McpFinding(
                    package=package,
                    file=rel_file,
                    line=lineno,
                    snippet=line.strip()[:120],
                )
            )
    return out


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


@dataclass
class McpEntry:
    package: str
    ecosystem: str = ""  # npm | pypi
    status: str = "unknown"  # archived | active | unknown
    since: str | None = None  # e.g. "2025-05" for the archive move
    replacement: str | None = None
    note: str = ""


@dataclass
class RegistryEntry:
    model: str
    provider: str
    status: str  # active | retired | retiring | deprecated | alias | unknown
    retirement_date: date | None = None
    days_left: int | None = None
    replacement: str | None = None
    match_type: str = "none"  # exact | family | none
    note: str = ""
    routes: dict = field(default_factory=dict)


class Registry:
    def __init__(self, data: dict):
        self.models: dict[str, dict] = {m["id"]: m for m in data.get("models", [])}
        self.families: list[dict] = data.get("families", [])
        self.param_deprecations: list[dict] = data.get("param_deprecations", [])
        self.mcp_servers: dict[str, dict] = {
            s["package"]: s for s in data.get("mcp_servers", [])
        }

    def lookup_mcp(self, package: str) -> McpEntry:
        raw = self.mcp_servers.get(package)
        if raw is None:
            return McpEntry(package=package)
        return McpEntry(
            package=package,
            ecosystem=raw.get("ecosystem", ""),
            status=raw.get("status", "unknown"),
            since=raw.get("since"),
            replacement=raw.get("replacement"),
            note=raw.get("note", ""),
        )

    @classmethod
    def load(cls, path: Path) -> "Registry":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def lookup(self, model: str, today: date) -> RegistryEntry:
        raw: dict | None = None
        match_type = "none"
        if model in self.models:
            raw = self.models[model]
            match_type = "exact"
        else:
            # Longest matching family prefix wins.
            best: dict | None = None
            for fam in self.families:
                if model.startswith(fam["prefix"]):
                    if best is None or len(fam["prefix"]) > len(best["prefix"]):
                        best = fam
            if best is not None:
                raw = {
                    "id": model,
                    "provider": best["provider"],
                    "retirement_date": best.get("retirement_date"),
                    "replacement": best.get("replacement"),
                    "note": best.get("note", ""),
                }
                match_type = "family"
        if raw is None:
            return RegistryEntry(model=model, provider="", status="unknown")

        entry = RegistryEntry(
            model=model,
            provider=raw.get("provider", ""),
            status="unknown",
            replacement=raw.get("replacement"),
            match_type=match_type,
            note=raw.get("note", ""),
            routes=raw.get("routes", {}),
        )
        explicit = raw.get("status")
        date_str = raw.get("retirement_date")
        if date_str:
            entry.retirement_date = date.fromisoformat(date_str)
            entry.days_left = (entry.retirement_date - today).days
            entry.status = "retired" if entry.days_left < 0 else "retiring"
        elif explicit:
            entry.status = explicit
        else:
            entry.status = "deprecated"
        return entry


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


@dataclass
class ModelReport:
    entry: RegistryEntry
    findings: list[Finding]


STATUS_ORDER = {"retired": 0, "retiring": 1, "deprecated": 2, "alias": 3,
                "active": 4, "unknown": 5}


MCP_STATUS_ORDER = {"archived": 0, "unknown": 1, "active": 2}


def build_report(findings: list[Finding], param_findings: list[ParamFinding],
                 registry: Registry, today: date, target: Path,
                 mcp_findings: list[McpFinding] | None = None) -> str:
    by_model: dict[str, list[Finding]] = {}
    for f in findings:
        by_model.setdefault(f.model, []).append(f)

    reports: list[ModelReport] = []
    for model, model_findings in by_model.items():
        reports.append(ModelReport(registry.lookup(model, today), model_findings))
    reports.sort(key=lambda r: (STATUS_ORDER.get(r.entry.status, 9), r.entry.model))

    counts: dict[str, int] = {}
    for r in reports:
        counts[r.entry.status] = counts.get(r.entry.status, 0) + 1

    lines: list[str] = []
    lines.append("# SunsetWatch report")
    lines.append("")
    lines.append(f"- Target: `{target}`")
    lines.append(f"- Scan date: {today.isoformat()}")
    lines.append(f"- Distinct models found: {len(reports)} "
                 f"({sum(len(r.findings) for r in reports)} references)")
    summary = ", ".join(f"{counts[s]} {s}" for s in
                        sorted(counts, key=lambda s: STATUS_ORDER.get(s, 9)))
    lines.append(f"- By status: {summary if summary else 'none'}")
    if mcp_findings:
        mcp_entries = {f.package: registry.lookup_mcp(f.package)
                       for f in mcp_findings}
        n_archived = sum(1 for e in mcp_entries.values()
                         if e.status == "archived")
        lines.append(f"- Distinct MCP servers found: {len(mcp_entries)} "
                     f"({len(mcp_findings)} references, "
                     f"{n_archived} archived)")
    lines.append("")

    lines.append("## Models")
    lines.append("")
    lines.append("| Model | Provider | Status | Retires | Days left | Replacement | Found in |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in reports:
        e = r.entry
        retires = e.retirement_date.isoformat() if e.retirement_date else "-"
        days = str(e.days_left) if e.days_left is not None else "-"
        replacement = e.replacement or "-"
        found = "<br>".join(f"{f.file}:{f.line}" for f in r.findings)
        status = e.status + (f" ({e.match_type} match)" if e.match_type == "family" else "")
        lines.append(f"| `{e.model}` | {e.provider or r.findings[0].provider_guess} "
                     f"| {status} | {retires} | {days} | {replacement} | {found} |")
    lines.append("")

    notes = [r for r in reports if r.entry.note]
    if notes:
        lines.append("## Registry notes")
        lines.append("")
        for r in notes:
            lines.append(f"- `{r.entry.model}`: {r.entry.note}")
            for route, rdate in r.entry.routes.items():
                lines.append(f"  - Route `{route}`: retires {rdate}")
        lines.append("")

    if mcp_findings:
        by_package: dict[str, list[McpFinding]] = {}
        for f in mcp_findings:
            by_package.setdefault(f.package, []).append(f)
        mcp_reports = sorted(
            ((registry.lookup_mcp(pkg), pkg_findings)
             for pkg, pkg_findings in by_package.items()),
            key=lambda r: (MCP_STATUS_ORDER.get(r[0].status, 9), r[0].package),
        )
        lines.append("## MCP servers")
        lines.append("")
        lines.append("MCP servers live in client configs, not dependency "
                     "files - an archived one keeps installing and running, "
                     "it just never gets security fixes or protocol updates.")
        lines.append("")
        lines.append("| Package | Ecosystem | Status | Since | Replacement | Found in |")
        lines.append("|---|---|---|---|---|---|")
        for entry, pkg_findings in mcp_reports:
            found = "<br>".join(f"{f.file}:{f.line}" for f in pkg_findings)
            lines.append(f"| `{entry.package}` | {entry.ecosystem or '-'} "
                         f"| {entry.status} | {entry.since or '-'} "
                         f"| {entry.replacement or '-'} | {found} |")
        lines.append("")
        mcp_notes = [e for e, _ in mcp_reports if e.note]
        if mcp_notes:
            lines.append("### MCP registry notes")
            lines.append("")
            for e in mcp_notes:
                lines.append(f"- `{e.package}`: {e.note}")
            lines.append("")

    if param_findings:
        lines.append("## Parameter deprecations")
        lines.append("")
        lines.append("Anthropic API version 2026-10-21 removes `temperature`, "
                     "`top_p`, `top_k` - requests sending them return a 400.")
        lines.append("")
        lines.append("| Param | Provider | API version | Found in |")
        lines.append("|---|---|---|---|")
        for p in param_findings:
            lines.append(f"| `{p.param}` | {p.provider} | {p.api_version} "
                         f"| {p.file}:{p.line} |")
        lines.append("")

    lines.append("---")
    lines.append("SunsetWatch POC - local scan against a hand-seeded registry. "
                 "Model dates come from the validation dossier (2026-11-08); "
                 "MCP archive status from the modelcontextprotocol/servers "
                 "README and the servers-archived repo (checked 2026-11-23). "
                 "An 'unknown' status means the item is not in the seed "
                 "registry, not that it is safe.")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sunsetwatch",
        description="Scan a directory for AI model IDs and MCP server "
                    "packages and check them against a local deprecation "
                    "registry (POC).",
    )
    parser.add_argument("target", type=Path, help="Directory to scan")
    parser.add_argument("--registry", type=Path,
                        default=Path(__file__).with_name("registry.json"),
                        help="Registry JSON (default: registry.json next to this script)")
    parser.add_argument("--out", type=Path, default=Path("sunsetwatch-report.md"),
                        help="Markdown report path (default: ./sunsetwatch-report.md)")
    parser.add_argument("--today", type=str, default=None,
                        help="Override scan date (YYYY-MM-DD) for reproducible runs")
    parser.add_argument("--fail-on-risk", action="store_true",
                        help="Exit 1 if any retired/retiring model or "
                             "archived MCP server is found")
    args = parser.parse_args(argv)

    if not args.target.is_dir():
        print(f"error: target {args.target} is not a directory", file=sys.stderr)
        return 2
    today = date.fromisoformat(args.today) if args.today else date.today()

    registry = Registry.load(args.registry)

    findings: list[Finding] = []
    param_findings: list[ParamFinding] = []
    mcp_findings: list[McpFinding] = []
    for path in iter_files(args.target):
        file_findings, text = scan_file(path, args.target)
        findings.extend(file_findings)
        if text:
            rel = str(path.relative_to(args.target))
            param_findings.extend(scan_params(text, rel, registry))
            mcp_findings.extend(scan_mcp(text, rel))

    report_md = build_report(findings, param_findings, registry, today,
                             args.target, mcp_findings=mcp_findings)
    args.out.write_text(report_md, encoding="utf-8")

    n_risky = len({f.model for f in findings
                   if registry.lookup(f.model, today).status in ("retired", "retiring")})
    n_archived_mcp = len({f.package for f in mcp_findings
                          if registry.lookup_mcp(f.package).status == "archived"})
    print(f"SunsetWatch: scanned {args.target} - "
          f"{len({f.model for f in findings})} distinct models, "
          f"{len(findings)} references, {len(param_findings)} parameter findings, "
          f"{len({f.package for f in mcp_findings})} distinct MCP servers "
          f"({n_archived_mcp} archived).")
    print(f"Report written to {args.out}")
    print()
    print(report_md)

    if args.fail_on_risk and (n_risky or n_archived_mcp):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

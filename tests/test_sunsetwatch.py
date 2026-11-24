"""Tests for SunsetWatch POC scanner + registry matching. Stdlib unittest only.

Run from the POC folder:  python3 -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sunsetwatch as sw  # noqa: E402

POC_DIR = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 8)  # fixed reference date for deterministic tests


def load_registry() -> sw.Registry:
    return sw.Registry.load(POC_DIR / "registry.json")


class TestRegistryLookup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = load_registry()

    def test_exact_match_retired(self):
        e = self.reg.lookup("gpt-4", TODAY)
        self.assertEqual(e.match_type, "exact")
        self.assertEqual(e.status, "retired")
        self.assertEqual(e.retirement_date, date(2026, 10, 23))
        self.assertLess(e.days_left, 0)

    def test_retiring_model_days_left(self):
        e = self.reg.lookup("claude-sonnet-4-5", TODAY)
        self.assertEqual(e.status, "retiring")
        self.assertEqual(e.retirement_date, date(2026, 11, 30))
        self.assertEqual(e.days_left, 22)
        # Per-route date from the dossier: Bedrock lives longer.
        self.assertEqual(e.routes.get("bedrock"), "2027-04-08")

    def test_family_match_for_dated_snapshot(self):
        e = self.reg.lookup("gpt-4o-2024-08-06", TODAY)
        self.assertEqual(e.match_type, "family")
        self.assertEqual(e.status, "retiring")
        self.assertEqual(e.retirement_date, date(2027, 1, 14))

    def test_exact_entry_beats_family_date(self):
        # gpt-4o-2024-05-13 matches the gpt-4o- family prefix but has its own
        # earlier exact date (2026-10-23, not 2027-01-14).
        e = self.reg.lookup("gpt-4o-2024-05-13", TODAY)
        self.assertEqual(e.match_type, "exact")
        self.assertEqual(e.retirement_date, date(2026, 10, 23))
        self.assertEqual(e.status, "retired")

    def test_gpt5_family(self):
        e = self.reg.lookup("gpt-5-mini", TODAY)
        self.assertEqual(e.match_type, "family")
        self.assertEqual(e.retirement_date, date(2026, 12, 11))

    def test_unknown_model(self):
        e = self.reg.lookup("text-embedding-3-small", TODAY)
        self.assertEqual(e.status, "unknown")
        self.assertEqual(e.match_type, "none")
        self.assertIsNone(e.retirement_date)

    def test_active_and_alias_entries(self):
        active = self.reg.lookup("gpt-6-luna", TODAY)
        self.assertEqual(active.status, "active")
        alias = self.reg.lookup("gpt-4o", TODAY)
        self.assertEqual(alias.status, "alias")
        self.assertEqual(alias.replacement, "gpt-6-luna")

    def test_retired_vertex_model(self):
        e = self.reg.lookup("gemini-2.5-flash", TODAY)
        self.assertEqual(e.status, "retired")
        self.assertEqual(e.retirement_date, date(2026, 10, 20))


class TestScanner(unittest.TestCase):
    def _scan(self, files: dict[str, str]):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        findings: list[sw.Finding] = []
        params: list[sw.ParamFinding] = []
        reg = load_registry()
        for path in sw.iter_files(root):
            ff, text = sw.scan_file(path, root)
            findings.extend(ff)
            params.extend(sw.scan_params(text, str(path.relative_to(root)), reg))
        return tmp, findings, params

    def test_finds_models_with_file_and_line(self):
        _, findings, _ = self._scan({
            "app.py": 'MODEL = "gpt-4"\nOTHER = "claude-sonnet-4-5"\n',
        })
        by_model = {f.model: f for f in findings}
        self.assertIn("gpt-4", by_model)
        self.assertEqual(by_model["gpt-4"].line, 1)
        self.assertEqual(by_model["gpt-4"].file, "app.py")
        self.assertIn("claude-sonnet-4-5", by_model)
        self.assertEqual(by_model["claude-sonnet-4-5"].line, 2)

    def test_scans_env_and_config_files(self):
        _, findings, _ = self._scan({
            ".env": "OPENAI_MODEL=gpt-3.5-turbo\n",
            "config/models.yaml": "model: gemini-2.5-pro\n",
            "notes.txt": "gpt-4 in a txt file is out of scope\n",
        })
        models = {f.model for f in findings}
        self.assertIn("gpt-3.5-turbo", models)
        self.assertIn("gemini-2.5-pro", models)
        self.assertNotIn("gpt-4", models)  # .txt not scanned

    def test_skips_node_modules_and_git(self):
        _, findings, _ = self._scan({
            "node_modules/pkg/index.js": 'const m = "gpt-4";\n',
            ".git/hooks/x.py": 'M = "gpt-4"\n',
            "src/ok.py": 'M = "gpt-4"\n',
        })
        files = {f.file for f in findings}
        self.assertEqual(files, {"src/ok.py"})

    def test_param_deprecation_flagged_only_for_anthropic(self):
        _, _, params = self._scan({
            "a.py": 'import anthropic\n'
                    'client.messages.create(model="claude-sonnet-4-5", temperature=0.7)\n',
            "b.py": 'from openai import OpenAI\n'
                    'client.chat.completions.create(model="gpt-4", temperature=0.7)\n',
        })
        self.assertEqual(len(params), 1)
        self.assertEqual(params[0].param, "temperature")
        self.assertEqual(params[0].file, "a.py")
        self.assertEqual(params[0].api_version, "2026-10-21")

    def test_dedupes_same_model_same_line(self):
        _, findings, _ = self._scan({
            "a.py": 'x = ("gpt-4", "gpt-4")\n',
        })
        self.assertEqual(len([f for f in findings if f.model == "gpt-4"]), 1)


class TestFixtureAndReport(unittest.TestCase):
    def test_fixture_scan_and_report(self):
        reg = load_registry()
        fixture = POC_DIR / "fixtures" / "sample-app"
        findings: list[sw.Finding] = []
        params: list[sw.ParamFinding] = []
        for path in sw.iter_files(fixture):
            ff, text = sw.scan_file(path, fixture)
            findings.extend(ff)
            params.extend(sw.scan_params(text, str(path.relative_to(fixture)), reg))

        models = {f.model for f in findings}
        for expected in ("claude-sonnet-4-5", "gpt-4", "gpt-4o-2024-08-06",
                         "gpt-3.5-turbo", "gpt-4o-2024-05-13", "gpt-image-1",
                         "gemini-2.5-pro", "text-embedding-3-small",
                         "mistral-large-latest"):
            self.assertIn(expected, models)
        self.assertTrue(any(p.param == "temperature" for p in params))

        report = sw.build_report(findings, params, reg, TODAY, fixture)
        self.assertIn("claude-sonnet-4-5", report)
        self.assertIn("2026-11-30", report)
        self.assertIn("retired", report)
        self.assertIn("unknown", report)


class TestMcpRegistry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = load_registry()

    def test_archived_npm_server_with_replacement(self):
        e = self.reg.lookup_mcp("@modelcontextprotocol/server-github")
        self.assertEqual(e.status, "archived")
        self.assertEqual(e.ecosystem, "npm")
        self.assertEqual(e.since, "2025-05")
        self.assertIn("github-mcp-server", e.replacement)

    def test_archived_pypi_server(self):
        e = self.reg.lookup_mcp("mcp-server-sqlite")
        self.assertEqual(e.status, "archived")
        self.assertEqual(e.ecosystem, "pypi")

    def test_maintained_servers_are_active_not_flagged(self):
        for pkg in ("@modelcontextprotocol/server-filesystem",
                    "@modelcontextprotocol/server-memory",
                    "mcp-server-git", "mcp-server-fetch", "mcp-server-time"):
            self.assertEqual(self.reg.lookup_mcp(pkg).status, "active", pkg)

    def test_unknown_mcp_package(self):
        e = self.reg.lookup_mcp("@modelcontextprotocol/server-nope")
        self.assertEqual(e.status, "unknown")
        self.assertIsNone(e.replacement)


class TestMcpScanner(unittest.TestCase):
    def test_scan_finds_packages_with_lines(self):
        text = ('{\n'
                '  "mcpServers": {\n'
                '    "gh": {"command": "npx", "args": ["-y", '
                '"@modelcontextprotocol/server-github"]},\n'
                '    "db": {"command": "uvx", "args": ["mcp-server-sqlite"]}\n'
                '  }\n'
                '}\n')
        found = sw.scan_mcp(text, ".mcp.json")
        by_pkg = {f.package: f for f in found}
        self.assertEqual(by_pkg["@modelcontextprotocol/server-github"].line, 3)
        self.assertEqual(by_pkg["mcp-server-sqlite"].line, 4)

    def test_scan_dedupes_same_package_same_line(self):
        text = 'x = "@modelcontextprotocol/server-redis @modelcontextprotocol/server-redis"\n'
        found = sw.scan_mcp(text, "a.py")
        self.assertEqual(len(found), 1)

    def test_fixture_report_has_mcp_section(self):
        reg = load_registry()
        fixture = POC_DIR / "fixtures" / "sample-app"
        mcp: list[sw.McpFinding] = []
        for path in sw.iter_files(fixture):
            _, text = sw.scan_file(path, fixture)
            if text:
                mcp.extend(sw.scan_mcp(text, str(path.relative_to(fixture))))
        packages = {f.package for f in mcp}
        self.assertIn("@modelcontextprotocol/server-github", packages)
        self.assertIn("mcp-server-sqlite", packages)
        report = sw.build_report([], [], reg, TODAY, fixture, mcp_findings=mcp)
        self.assertIn("## MCP servers", report)
        self.assertIn("archived", report)
        # Maintained server shows as active, not archived.
        self.assertIn("@modelcontextprotocol/server-filesystem", report)

    def test_fail_on_risk_trips_on_archived_mcp(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".mcp.json").write_text(
                '{"mcpServers": {"pg": {"args": '
                '["@modelcontextprotocol/server-postgres"]}}}\n',
                encoding="utf-8")
            out = root / "report.md"
            with contextlib.redirect_stdout(io.StringIO()):
                code = sw.main([str(root), "--out", str(out),
                                "--today", TODAY.isoformat(),
                                "--fail-on-risk"])
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()

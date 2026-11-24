"""Tests for the SunsetWatch guard hook (sunsetwatch_guard.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "sunsetwatch_guard.py"
REGISTRY = ROOT / "registry.json"
sys.path.insert(0, str(ROOT))

import sunsetwatch_guard as g  # noqa: E402

ENTRIES = g.load_entries(REGISTRY)


def ids(hits):
    return sorted(e.id for e in hits)


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/tmp"}


class BlockedCommands(unittest.TestCase):
    CASES = [
        ("npx -y @modelcontextprotocol/server-github", "github"),
        ("npx @modelcontextprotocol/server-github@latest", "github"),
        ("npx -y @modelcontextprotocol/server-puppeteer@2025.5.12", "puppeteer"),
        ("npx --yes --package=@modelcontextprotocol/server-postgres mcp-server-postgres postgres://x", "postgres"),
        ("npx -p @modelcontextprotocol/server-slack mcp-server-slack", "slack"),
        ("npm install -g @modelcontextprotocol/server-brave-search", "brave-search"),
        ("npm i @modelcontextprotocol/server-gitlab --save", "gitlab"),
        ("npm exec -- @modelcontextprotocol/server-gdrive", "gdrive"),
        ("pnpm dlx @modelcontextprotocol/server-google-maps", "google-maps"),
        ("pnpm add -D @modelcontextprotocol/server-redis", "redis"),
        ("yarn dlx @modelcontextprotocol/server-everart", "everart"),
        ("yarn global add @modelcontextprotocol/server-aws-kb-retrieval", "aws-kb-retrieval"),
        ("bunx @modelcontextprotocol/server-github", "github"),
        ("bun add @modelcontextprotocol/server-github", "github"),
        ("uvx mcp-server-sqlite --db-path ~/test.db", "sqlite"),
        ("uvx mcp-server-sentry@latest --auth-token x", "sentry"),
        ("uvx --from mcp-server-sentry mcp-server-sentry", "sentry"),
        ("pip install mcp-server-sqlite", "sqlite"),
        ("pip3 install 'mcp-server-sqlite==2025.4.25'", "sqlite"),
        ("python -m pip install mcp_server_sentry", "sentry"),
        ("python3.12 -m pip install --user MCP-Server-SQLite", "sqlite"),
        ("uv pip install mcp-server-sqlite", "sqlite"),
        ("uv tool install mcp-server-sentry", "sentry"),
        ("uv run --with mcp-server-sqlite python app.py", "sqlite"),
        ("pipx run mcp-server-sqlite", "sqlite"),
        ("claude mcp add github -- npx -y @modelcontextprotocol/server-github", "github"),
        ("claude mcp add --scope user puppeteer npx @modelcontextprotocol/server-puppeteer", "puppeteer"),
        (
            'claude mcp add-json github \'{"command":"npx","args":["-y","@modelcontextprotocol/server-github"]}\'',
            "github",
        ),
        ("cd ~/proj && npx -y @modelcontextprotocol/server-github", "github"),
        ("cd ~/proj\nnpx -y @modelcontextprotocol/server-github", "github"),
        ("GITHUB_PERSONAL_ACCESS_TOKEN=x npx -y @modelcontextprotocol/server-github", "github"),
        ("sudo npm install -g @modelcontextprotocol/server-github", "github"),
        ("env FOO=1 npx @modelcontextprotocol/server-github", "github"),
        ("bash -c 'npx -y @modelcontextprotocol/server-github'", "github"),
        ('sh -c "cd /x && uvx mcp-server-sqlite"', "sqlite"),
        ("eval npx @modelcontextprotocol/server-github", "github"),
        ("echo @modelcontextprotocol/server-github | xargs npx -y", "github"),
        ("(npx -y @modelcontextprotocol/server-github)", "github"),
        ("npx -y @modelcontextprotocol/server-github 2>&1 | tee log.txt", "github"),
        ("npx.cmd -y @modelcontextprotocol/server-github", "github"),
        ("npx -y npm:@modelcontextprotocol/server-github", "github"),
        ("npx 'unbalanced @modelcontextprotocol/server-github", "github"),
    ]

    def test_blocked(self):
        for command, expected in self.CASES:
            with self.subTest(command=command):
                self.assertIn(expected, ids(g.check_command(command, ENTRIES)))

    def test_two_in_one_command(self):
        hits = g.check_command(
            "npm i -g @modelcontextprotocol/server-github @modelcontextprotocol/server-slack", ENTRIES
        )
        self.assertEqual(ids(hits), ["github", "slack"])


class AllowedCommands(unittest.TestCase):
    CASES = [
        # Active reference servers.
        "npx -y @modelcontextprotocol/server-filesystem ~/Desktop",
        "npx -y @modelcontextprotocol/server-memory",
        "npx -y @modelcontextprotocol/server-everything",
        "npx -y @modelcontextprotocol/server-sequential-thinking",
        "uvx mcp-server-git --repository .",
        "uvx mcp-server-fetch",
        "uvx mcp-server-time",
        # Replacements.
        "npx -y @brave/brave-search-mcp-server",
        "npx -y @playwright/mcp@latest",
        "npx @sentry/mcp-server@latest",
        "uvx --from redis-mcp-server@latest redis-mcp-server --url redis://localhost:6379/0",
        "claude mcp add --transport http github https://api.githubcopilot.com/mcp/",
        "npx -y mcp-remote https://gitlab.com/api/v4/mcp",
        # Reading about or removing an archived package is fine.
        "npm view @modelcontextprotocol/server-github deprecated",
        "npm info @modelcontextprotocol/server-puppeteer",
        "npm uninstall -g @modelcontextprotocol/server-github",
        "pip uninstall -y mcp-server-sqlite",
        "pip show mcp-server-sentry",
        "uv pip uninstall mcp-server-sqlite",
        "claude mcp remove github",
        "grep -r @modelcontextprotocol/server-github .",
        "echo 'never use @modelcontextprotocol/server-github'",
        "git log --grep mcp-server-sqlite",
        # Lookalike names are not the archived package.
        "npx -y @modelcontextprotocol/server-github-extra",
        "uvx mcp-server-sqlite-plus",
        "npx -y server-github",
    ]

    def test_allowed(self):
        for command in self.CASES:
            with self.subTest(command=command):
                self.assertEqual(ids(g.check_command(command, ENTRIES)), [])


class ConfigEdits(unittest.TestCase):
    GITHUB_SERVER = {
        "mcpServers": {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "x"},
            }
        }
    }

    def write(self, path, content):
        return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}

    def test_config_paths(self):
        for path in [
            "/repo/.mcp.json",
            "/home/me/.claude.json",
            "/repo/.cursor/mcp.json",
            "/home/me/.cursor/mcp.json",
            "/repo/.vscode/mcp.json",
            "/Users/me/Library/Application Support/Claude/claude_desktop_config.json",
            "C:\\Users\\me\\AppData\\Roaming\\Claude\\claude_desktop_config.json",
            "/home/me/.codeium/windsurf/mcp_config.json",
            "/home/me/.codex/config.toml",
            "/repo/package.json",
            "/repo/requirements-dev.txt",
        ]:
            with self.subTest(path=path):
                self.assertTrue(g.is_config_path(path))
        for path in ["/repo/README.md", "/repo/src/config.toml", "/repo/notes.json"]:
            with self.subTest(path=path):
                self.assertFalse(g.is_config_path(path))

    def test_write_mcp_json_blocked(self):
        for path in ["/repo/.mcp.json", "/home/me/.cursor/mcp.json", "/x/claude_desktop_config.json"]:
            with self.subTest(path=path):
                reason, hits = g.evaluate(self.write(path, json.dumps(self.GITHUB_SERVER, indent=2)), ENTRIES, set())
                self.assertEqual(ids(hits), ["github"])
                self.assertIn("github/github-mcp-server", reason)

    def test_edit_adds_server(self):
        event = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/repo/.mcp.json",
                "old_string": '"mcpServers": {',
                "new_string": '"mcpServers": {\n    "pg": {"command": "npx", "args": ["-y", '
                '"@modelcontextprotocol/server-postgres@0.6.2", "postgresql://localhost/db"]},',
            },
        }
        reason, hits = g.evaluate(event, ENTRIES, set())
        self.assertEqual(ids(hits), ["postgres"])

    def test_multiedit_and_python_server(self):
        event = {
            "tool_name": "MultiEdit",
            "tool_input": {
                "file_path": "/repo/.cursor/mcp.json",
                "edits": [
                    {"old_string": "a", "new_string": '"sqlite": {"command": "uvx", "args": ["mcp-server-sqlite"]}'}
                ],
            },
        }
        self.assertEqual(ids(g.evaluate(event, ENTRIES, set())[1]), ["sqlite"])

    def test_node_modules_path_in_config(self):
        script = "/usr/lib/node_modules/@modelcontextprotocol/server-github/dist/index.js"
        content = json.dumps({"mcpServers": {"gh": {"command": "node", "args": [script]}}})
        self.assertEqual(ids(g.evaluate(self.write("/repo/.mcp.json", content), ENTRIES, set())[1]), ["github"])

    def test_codex_toml(self):
        content = '[mcp_servers.github]\ncommand = "npx"\nargs = ["-y", "@modelcontextprotocol/server-github"]\n'
        self.assertEqual(
            ids(g.evaluate(self.write("/home/me/.codex/config.toml", content), ENTRIES, set())[1]), ["github"]
        )

    def test_package_json_dependency(self):
        content = '{"dependencies": {"@modelcontextprotocol/server-slack": "^2025.4.25"}}'
        self.assertEqual(ids(g.evaluate(self.write("/repo/package.json", content), ENTRIES, set())[1]), ["slack"])

    def test_replacement_config_allowed(self):
        content = json.dumps(
            {
                "mcpServers": {
                    "github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"},
                    "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
                    "git": {"command": "uvx", "args": ["mcp-server-git"]},
                }
            }
        )
        self.assertEqual(g.evaluate(self.write("/repo/.mcp.json", content), ENTRIES, set()), (None, []))

    def test_non_config_file_ignored(self):
        content = "Do not use @modelcontextprotocol/server-github."
        self.assertEqual(g.evaluate(self.write("/repo/README.md", content), ENTRIES, set()), (None, []))


class Allowlist(unittest.TestCase):
    def test_allowed_package_passes(self):
        allowed = {"@modelcontextprotocol/server-github"}
        self.assertEqual(g.evaluate(bash("npx -y @modelcontextprotocol/server-github"), ENTRIES, allowed), (None, []))

    def test_allow_file_and_env(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, g.ALLOW_FILE).write_text("# reviewed by me\nmcp_server_sqlite  # pinned for a demo\n")
            with mock.patch.dict(os.environ, {g.ALLOW_ENV: "@modelcontextprotocol/server-slack"}, clear=False):
                allowed = g.load_allowed(d)
        self.assertIn("mcp-server-sqlite", allowed)
        self.assertIn("@modelcontextprotocol/server-slack", allowed)
        reason, _ = g.evaluate(bash("uvx mcp-server-sqlite"), ENTRIES, allowed)
        self.assertIsNone(reason)

    def test_agent_cannot_edit_allow_file(self):
        for event in [
            {"tool_name": "Write", "tool_input": {"file_path": "/repo/.sunsetwatch-allow", "content": "x"}},
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "/home/me/.config/sunsetwatch/allow", "new_string": "x"},
            },
            bash("echo @modelcontextprotocol/server-github >> .sunsetwatch-allow"),
            bash("printf 'x' | tee -a .sunsetwatch-allow"),
        ]:
            with self.subTest(event=event):
                reason, _ = g.evaluate(event, ENTRIES, set())
                self.assertEqual(reason, g.ALLOW_FILE_MESSAGE)

    def test_reading_allow_file_is_fine(self):
        self.assertEqual(g.evaluate(bash("cat .sunsetwatch-allow 2>&1"), ENTRIES, set()), (None, []))


class HookProcess(unittest.TestCase):
    """Run the script the way Claude Code does: JSON on stdin, exit code out."""

    def run_hook(self, event, env=None):
        full_env = dict(os.environ)
        full_env.pop(g.ALLOW_ENV, None)
        full_env.pop(g.MODE_ENV, None)
        full_env["HOME"] = tempfile.mkdtemp()  # no user allowlist
        full_env.update(env or {})
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(event) if not isinstance(event, str) else event,
            capture_output=True,
            text=True,
            env=full_env,
            timeout=20,
        )

    def test_block_exits_2_with_replacement(self):
        p = self.run_hook(bash("npx -y @modelcontextprotocol/server-github"))
        self.assertEqual(p.returncode, 2)
        self.assertIn("github/github-mcp-server", p.stderr)
        self.assertIn("Do not retry", p.stderr)

    def test_allowed_exits_0(self):
        p = self.run_hook(bash("npx -y @modelcontextprotocol/server-filesystem ."))
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))

    def test_other_tools_ignored(self):
        p = self.run_hook({"tool_name": "Read", "tool_input": {"file_path": "/repo/.mcp.json"}})
        self.assertEqual(p.returncode, 0)

    def test_ask_mode(self):
        p = self.run_hook(bash("uvx mcp-server-sqlite"), {g.MODE_ENV: "ask"})
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "ask")
        self.assertIn("mcp-server-sqlite", out["permissionDecisionReason"])

    def test_off_mode(self):
        p = self.run_hook(bash("npx -y @modelcontextprotocol/server-github"), {g.MODE_ENV: "off"})
        self.assertEqual(p.returncode, 0)

    def test_bad_input_fails_open(self):
        self.assertEqual(self.run_hook("not json").returncode, 0)

    def test_missing_list_is_a_visible_error(self):
        p = self.run_hook(bash("npx -y @modelcontextprotocol/server-github"), {g.REGISTRY_ENV: "/nope.json"})
        self.assertEqual(p.returncode, 1)
        self.assertIn("could not load", p.stderr)

    def test_cli_check_command(self):
        p = subprocess.run(
            [sys.executable, str(HOOK), "--check-command", "npx -y @modelcontextprotocol/server-puppeteer"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(p.returncode, 1)
        self.assertIn("playwright-mcp", p.stdout)
        self.assertIn("remote code execution", p.stdout)


class RegistryList(unittest.TestCase):
    """The guard and the scanner share registry.json's mcp_servers list."""

    def setUp(self):
        self.servers = json.loads(REGISTRY.read_text(encoding="utf-8"))["mcp_servers"]

    def test_guard_blocks_every_archived_server_and_nothing_else(self):
        archived = {s["package"] for s in self.servers if s["status"] == "archived"}
        self.assertEqual({e.package for e in ENTRIES}, archived)
        self.assertEqual(len(archived), 14)

    def test_every_archived_entry_cites_sources(self):
        for s in self.servers:
            if s["status"] != "archived":
                continue
            with self.subTest(package=s["package"]):
                self.assertIn(s["ecosystem"], {"npm", "pypi"})
                self.assertTrue(s["sources"])
                self.assertTrue(all(src.startswith("https://") for src in s["sources"]))

    def test_active_servers_pass(self):
        for s in self.servers:
            if s["status"] != "active":
                continue
            runner = "npx -y" if s["ecosystem"] == "npm" else "uvx"
            with self.subTest(package=s["package"]):
                self.assertEqual(g.check_command(f"{runner} {s['package']}", ENTRIES), [])

    def test_create_server_scaffold_blocked(self):
        hits = g.check_command("npx @modelcontextprotocol/create-server my-server", ENTRIES)
        self.assertEqual(ids(hits), ["create-server"])


if __name__ == "__main__":
    unittest.main()


class Hardening(unittest.TestCase):
    """Install shapes and edge cases found in the November 2026 stress test."""

    BLOCKED = [
        ("echo `npx -y @modelcontextprotocol/server-github`", "github"),
        ('echo "$(npx -y @modelcontextprotocol/server-github)"', "github"),
        ('echo "`uvx mcp-server-sqlite`"', "sqlite"),
        ("bash -lc 'npx -y @modelcontextprotocol/server-github'", "github"),
        ("sh -ec 'uvx mcp-server-sqlite'", "sqlite"),
        ('powershell -Command "npx -y @modelcontextprotocol/server-github"', "github"),
        ("pwsh -c 'npx -y @modelcontextprotocol/server-github'", "github"),
        ("cmd /c npx -y @modelcontextprotocol/server-github", "github"),
        ("echo 'npx -y @modelcontextprotocol/server-github' | sh", "github"),
        ("printf 'uvx mcp-server-sqlite\\n' | bash -s", "sqlite"),
        ("deno run -A npm:@modelcontextprotocol/server-github", "github"),
        ("corepack pnpm dlx @modelcontextprotocol/server-github", "github"),
        ("npm init @modelcontextprotocol/server my-server", "create-server"),
        ("npm create @modelcontextprotocol/server my-server", "create-server"),
        ("npm install @modelcontextprotocol/server-github#main", "github"),
        ("npm i gh@npm:@modelcontextprotocol/server-github", "github"),
        (
            "npm i https://registry.npmjs.org/@modelcontextprotocol/server-github/-/server-github-2025.4.8.tgz",
            "github",
        ),
        ("poetry add mcp-server-sqlite", "sqlite"),
        ("pdm add mcp-server-sentry", "sentry"),
        ("conda install -c conda-forge mcp-server-sqlite", "sqlite"),
        ("P=@modelcontextprotocol/server-github; npx -y $P", "github"),
        (
            '"C:\\Program Files\\nodejs\\npx.cmd" -y @modelcontextprotocol/server-github',
            "github",
        ),
        ("C:/nodejs/npx.cmd -y @modelcontextprotocol/server-github", "github"),
    ]

    def test_blocked(self):
        for command, expected in self.BLOCKED:
            with self.subTest(command=command):
                self.assertIn(expected, ids(g.check_command(command, ENTRIES)))

    def test_deep_nesting(self):
        import shlex

        cmd = "npx -y @modelcontextprotocol/server-github"
        for _ in range(10):
            cmd = "bash -c " + shlex.quote(cmd)
        self.assertEqual(ids(g.check_command(cmd, ENTRIES)), ["github"])

    def test_comments_are_not_commands(self):
        for cmd in (
            "echo hello # npx @modelcontextprotocol/server-github",
            "npm i -D prettier # @modelcontextprotocol/server-github later",
            "# npx -y @modelcontextprotocol/server-github\nls",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(ids(g.check_command(cmd, ENTRIES)), [])
        # A quoted # is not a comment.
        self.assertEqual(
            ids(g.check_command("npx '#' @modelcontextprotocol/server-github", ENTRIES)),
            ["github"],
        )

    def test_shell_writes_to_mcp_config(self):
        server = '{"mcpServers":{"gh":{"command":"npx","args":["-y","@modelcontextprotocol/server-github"]}}}'
        for cmd in (
            f"echo '{server}' > .mcp.json",
            f"cat > .mcp.json <<EOF\n{server}\nEOF",
            f"echo '{server}' | tee ~/.cursor/mcp.json",
            f"jq '. + {server}' .mcp.json > t && mv t .mcp.json",
            "python3 -c \"import json;json.dump({'args':['@modelcontextprotocol/server-github']},open('.mcp.json','w'))\"",
        ):
            with self.subTest(cmd=cmd[:40]):
                reason, hits = g.evaluate(bash(cmd), ENTRIES, set())
                self.assertEqual(ids(hits), ["github"])

    def test_reading_config_is_fine(self):
        for cmd in (
            "cat .mcp.json",
            "grep -n @modelcontextprotocol/server-github .mcp.json",
            "jq . .mcp.json",
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNone(g.evaluate(bash(cmd), ENTRIES, set())[0])

    def test_more_config_files(self):
        for path in (
            ".vscode/settings.json",
            "/home/u/.gemini/settings.json",
            "/home/u/.config/zed/settings.json",
            ".continue/mcpServers/github.yaml",
            "cline_mcp_settings.json",
            "opencode.json",
            "/home/u/.config/goose/config.yaml",
            ".MCP.JSON",
        ):
            with self.subTest(path=path):
                self.assertTrue(g.is_config_path(path))
        self.assertFalse(g.is_config_path("src/settings.json"))

    def test_allowlist_cannot_be_written_from_the_shell(self):
        for cmd in (
            "python3 -c \"open('.sunsetwatch-allow','a').write('x')\"",
            "printf '%s\\n' x>>.sunsetwatch-allow",
            "cp /tmp/x .sunsetwatch-allow",
            "echo x | tee -a ~/.config/sunsetwatch/allow",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(g.evaluate(bash(cmd), ENTRIES, set())[0], g.ALLOW_FILE_MESSAGE)
        for cmd in (
            "cat .sunsetwatch-allow",
            "git diff .sunsetwatch-allow",
            "ls -la .sunsetwatch-allow",
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNone(g.evaluate(bash(cmd), ENTRIES, set())[0])

    def test_agent_cannot_switch_the_guard_off(self):
        settings = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/p/.claude/settings.local.json",
                "content": '{"env": {"SUNSETWATCH_GUARD_MODE": "off"}}',
            },
        }
        self.assertEqual(g.evaluate(settings, ENTRIES, set())[0], g.SWITCH_MESSAGE)
        disable = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/p/.claude/settings.json",
                "old_string": "true",
                "new_string": '"sunsetwatch@sunsetwatch": false',
            },
        }
        self.assertEqual(g.evaluate(disable, ENTRIES, set())[0], g.SWITCH_MESSAGE)
        for cmd in (
            "claude plugin disable sunsetwatch@sunsetwatch",
            "claude plugin uninstall sunsetwatch",
            "jq '.env.SUNSETWATCH_GUARD_MODE=\"off\"' .claude/settings.json > t && mv t .claude/settings.json",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(g.evaluate(bash(cmd), ENTRIES, set())[0], g.SWITCH_MESSAGE)
        ordinary = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/p/.claude/settings.json",
                "content": '{"permissions": {}}',
            },
        }
        self.assertIsNone(g.evaluate(ordinary, ENTRIES, set())[0])
        self.assertIsNone(g.evaluate(bash("claude plugin list"), ENTRIES, set())[0])

    def test_odd_input_never_crashes(self):
        events = [
            {
                "tool_name": "MultiEdit",
                "tool_input": {"file_path": ".mcp.json", "edits": "abc"},
            },
            {
                "tool_name": "MultiEdit",
                "tool_input": {"file_path": ".mcp.json", "edits": [1, None]},
            },
            {"tool_name": "Bash", "tool_input": {"command": "npx x"}, "cwd": 123},
            {"tool_name": "Write", "tool_input": {"file_path": None, "content": None}},
        ]
        for event in events:
            with self.subTest(event=event):
                g.evaluate(event, ENTRIES, g.load_allowed(event.get("cwd")))

    def test_bad_registry_shapes(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "r.json"
            bad.write_text('{"mcp_servers": {"a": 1}}')
            with self.assertRaises(ValueError):
                g.load_entries(bad)
            bad.write_text('{"mcp_servers": [{"status": "archived"}, "x", {"status": "archived", "package": "p"}]}')
            self.assertEqual(g.load_entries(bad), [])

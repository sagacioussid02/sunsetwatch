"""Tests for the Claude Code plugin packaging (.claude-plugin/, hooks/)."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
HOOKS = ROOT / "hooks" / "hooks.json"


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def pre_tool_use_hooks() -> list[dict]:
    return load(HOOKS)["hooks"]["PreToolUse"]


def hook_argv(command: str) -> list[str]:
    """The hook command as argv, with ${CLAUDE_PLUGIN_ROOT} set to this repo."""
    return shlex.split(command.replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT)))


class Manifests(unittest.TestCase):
    def test_plugin_and_marketplace_names_match(self):
        plugin = load(PLUGIN)
        market = load(MARKETPLACE)
        entries = [p for p in market["plugins"] if p["name"] == plugin["name"]]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source"], ".")
        self.assertTrue(market["owner"]["name"])

    def test_hooks_wrap_event_map_and_match_guard_tools(self):
        groups = pre_tool_use_hooks()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "Bash|Write|Edit|MultiEdit")


class HookCommand(unittest.TestCase):
    def test_command_points_at_the_guard_script_beside_the_registry(self):
        hook = pre_tool_use_hooks()[0]["hooks"][0]
        self.assertEqual(hook["type"], "command")
        argv = hook_argv(hook["command"])
        script = Path(argv[-1])
        self.assertEqual(script, ROOT / "sunsetwatch_guard.py")
        self.assertTrue(script.is_file())
        self.assertTrue(script.with_name("registry.json").is_file())

    def test_command_blocks_an_archived_server(self):
        hook = pre_tool_use_hooks()[0]["hooks"][0]
        argv = hook_argv(hook["command"])
        argv[0] = sys.executable  # the python3 on PATH may not be this one
        event = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "npx -y @modelcontextprotocol/server-github"},
            "cwd": str(ROOT),
        }
        env = {k: v for k, v in os.environ.items() if not k.startswith("SUNSETWATCH_")}
        result = subprocess.run(
            argv, input=json.dumps(event), capture_output=True, text=True, env=env, check=False
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("github/github-mcp-server", result.stderr)


if __name__ == "__main__":
    unittest.main()

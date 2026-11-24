#!/usr/bin/env python3
"""SunsetWatch guard - a Claude Code hook that stops a coding agent from
installing archived MCP servers.

`sunsetwatch.py` reports archived MCP servers that are already in a
codebase. This hook stops new ones going in. It runs as a Claude Code
PreToolUse hook: it reads the hook event as JSON on stdin and blocks a tool
call that would install or run an archived MCP server, or add one to an MCP
config file. The block message names the maintained replacement from
`registry.json`, so the agent can switch to it.

Same registry as the scanner (`mcp_servers` entries with status
"archived"), stdlib only, Python 3.10+, no network.

Also usable from a shell:
    python3 sunsetwatch_guard.py --check-command "npx -y @modelcontextprotocol/server-github"
    python3 sunsetwatch_guard.py --list
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REGISTRY_ENV = "SUNSETWATCH_REGISTRY"
MODE_ENV = "SUNSETWATCH_GUARD_MODE"  # block (default) | ask | off
ALLOW_ENV = "SUNSETWATCH_GUARD_ALLOW"  # comma-separated package names
ALLOW_FILE = ".sunsetwatch-allow"
USER_ALLOW_FILE = Path("~/.config/sunsetwatch/allow")
DEFAULT_REGISTRY = Path(__file__).resolve().with_name("registry.json")

# Files that configure MCP servers or declare dependencies.
CONFIG_BASENAMES = {
    ".mcp.json",  # Claude Code project config
    ".claude.json",  # Claude Code user config
    "mcp.json",  # Cursor (.cursor/mcp.json), VS Code (.vscode/mcp.json)
    "claude_desktop_config.json",  # Claude Desktop
    "mcp_config.json",  # Windsurf
    "package.json",
    "pyproject.toml",
}
CONFIG_PATTERNS = [
    re.compile(r"(^|[\\/])\.codex[\\/]config\.toml$"),  # Codex CLI
    re.compile(r"(^|[\\/])requirements[^\\/]*\.txt$"),
]

# Commands that run whatever package they are given.
RUNNERS = {"npx", "bunx", "pnpx", "uvx"}
NODE_MANAGERS = {"npm", "pnpm", "yarn", "bun"}
NODE_READ_ONLY = {
    "view", "v", "info", "show", "search", "s", "se", "find", "docs", "home",
    "repo", "bugs", "issues", "outdated", "ls", "list", "ll", "la", "why",
    "explain", "uninstall", "remove", "rm", "r", "un", "unlink", "audit",
    "deprecate", "owner", "access", "dist-tag", "help",
}
PIP_READ_ONLY = {"show", "uninstall", "list", "freeze", "search", "index", "help", "check"}
PIPX_READ_ONLY = {"uninstall", "uninstall-all", "list", "help", "environment"}
WRAPPERS = {"sudo", "env", "command", "exec", "time", "nohup", "nice", "doas"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;", "{", "}"}
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


# ---------------------------------------------------------------- the list


class Entry:
    """One archived MCP server from registry.json."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.ecosystem: str = raw["ecosystem"]
        self.package: str = raw["package"]
        # Short name, e.g. "github" for @modelcontextprotocol/server-github.
        self.id = re.sub(r"^(@modelcontextprotocol/(server-)?|mcp-server-)", "", self.package)
        self.key = normalize_name(self.package, self.ecosystem)
        self.pattern = _text_pattern(self.package, self.ecosystem)


def normalize_name(name: str, ecosystem: str) -> str:
    if ecosystem == "pypi":
        return re.sub(r"[-_.]+", "-", name).lower()
    return name.lower()


def _text_pattern(package: str, ecosystem: str) -> re.Pattern[str]:
    if ecosystem == "pypi":
        body = r"[-_.]".join(re.escape(p) for p in re.split(r"[-_.]+", package))
        return re.compile(r"(?<![A-Za-z0-9_.-])" + body + r"(?![A-Za-z0-9_-])", re.IGNORECASE)
    return re.compile(r"(?<![A-Za-z0-9_.@-])" + re.escape(package) + r"(?![A-Za-z0-9_-])", re.IGNORECASE)


def load_entries(path: Path | None = None) -> list[Entry]:
    """The archived MCP servers in the SunsetWatch registry."""
    if path is None:
        path = Path(os.environ.get(REGISTRY_ENV) or DEFAULT_REGISTRY)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return [Entry(raw) for raw in data.get("mcp_servers", []) if raw.get("status") == "archived"]


def load_allowed(cwd: str | None = None) -> set[str]:
    """Package names a person has allowed. Matched against both ecosystems."""
    names: list[str] = []
    names += [n for n in os.environ.get(ALLOW_ENV, "").split(",")]
    files = [USER_ALLOW_FILE.expanduser()]
    for base in {os.environ.get("CLAUDE_PROJECT_DIR"), cwd}:
        if base:
            files.append(Path(base) / ALLOW_FILE)
    for f in files:
        try:
            names += f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
    allowed: set[str] = set()
    for n in names:
        n = n.split("#", 1)[0].strip()
        if n:
            allowed.add(normalize_name(n, "npm"))
            allowed.add(normalize_name(n, "pypi"))
    return allowed


# ---------------------------------------------------------------- matching


def _npm_name(token: str) -> str:
    t = token.strip()
    if t.startswith("npm:"):
        t = t[4:]
    if t.startswith("@"):
        slash = t.find("/")
        if slash != -1:
            at = t.find("@", slash)
            if at != -1:
                t = t[:at]
    elif "@" in t:
        t = t.split("@", 1)[0]
    return t.lower()


def _py_name(token: str) -> str:
    t = token.strip()
    t = re.split(r"\[|==|>=|<=|~=|!=|===|<|>|@|;|\s", t, maxsplit=1)[0]
    return normalize_name(t, "pypi")


def match_token(token: str, entries: list[Entry]) -> Entry | None:
    candidates = [token]
    if token.startswith("-") and "=" in token:
        candidates.append(token.split("=", 1)[1])
    for cand in candidates:
        if not cand or cand.startswith("-"):
            continue
        npm, py = _npm_name(cand), _py_name(cand)
        for e in entries:
            if (e.ecosystem == "npm" and npm == e.key) or (e.ecosystem == "pypi" and py == e.key):
                return e
    return None


def match_text(text: str, entries: list[Entry]) -> list[Entry]:
    return [e for e in entries if e.pattern.search(text)]


# ---------------------------------------------------------------- shell parsing


def _unquoted_newlines_to_semicolons(command: str) -> str:
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(command):
        c = command[i]
        if c == "\\" and quote != "'" and i + 1 < len(command):
            if command[i + 1] == "\n":  # line continuation
                i += 2
                continue
            out.append(command[i : i + 2])
            i += 2
            continue
        if quote:
            if c == quote:
                quote = ""
        elif c in ("'", '"'):
            quote = c
        elif c == "\n":
            c = ";"
        out.append(c)
        i += 1
    return "".join(out)


def split_segments(command: str) -> list[list[str]] | None:
    """Split a shell command into simple commands. None if it can't be parsed."""
    lexer = shlex.shlex(_unquoted_newlines_to_semicolons(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = [[]]
    for tok in tokens:
        if tok in SEPARATORS or (tok and all(ch in "();&|<>" for ch in tok) and not tok.startswith((">", "<"))):
            segments.append([])
        else:
            segments[-1].append(tok)
    return [s for s in segments if s]


def _strip_prefix(words: list[str]) -> list[str]:
    i = 0
    while i < len(words):
        w = words[i]
        if ENV_ASSIGN.match(w):
            i += 1
        elif os.path.basename(w) in WRAPPERS:
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 1
        else:
            break
    return words[i:]


def _first_word(words: Iterable[str]) -> str | None:
    for w in words:
        if not w.startswith("-"):
            return w
    return None


def is_install(words: list[str]) -> bool:
    """True when this simple command installs or runs packages it names."""
    w = _strip_prefix(words)
    if not w:
        return False
    cmd = os.path.basename(w[0]).lower()
    rest = w[1:]
    if re.fullmatch(r"python[0-9.]*|py", cmd):
        if "-m" not in rest:
            return False
        i = rest.index("-m")
        if i + 1 >= len(rest):
            return False
        cmd, rest = rest[i + 1].lower(), rest[i + 2 :]
    if cmd.endswith(".exe") or cmd.endswith(".cmd"):
        cmd = cmd.rsplit(".", 1)[0]
    if cmd in RUNNERS:
        return True
    sub = _first_word(rest)
    if cmd in NODE_MANAGERS:
        return sub is not None and sub.lower() not in NODE_READ_ONLY
    if re.fullmatch(r"pip[0-9.]*", cmd):
        return sub is not None and sub.lower() not in PIP_READ_ONLY
    if cmd == "pipx":
        return sub is not None and sub.lower() not in PIPX_READ_ONLY
    if cmd == "uv":
        lowered = [r.lower() for r in rest]
        return (
            sub is not None and sub.lower() != "remove" and not {"uninstall", "show", "list", "freeze"} & set(lowered)
        )
    if cmd == "claude":
        return len(rest) >= 2 and rest[0] == "mcp" and rest[1].startswith("add")
    return False


def check_command(command: str, entries: list[Entry], depth: int = 0) -> list[Entry]:
    hits: list[Entry] = []

    def add(e: Entry | None) -> None:
        if e is not None and e not in hits:
            hits.append(e)

    segments = split_segments(command)
    if segments is None:
        # Unparseable (unbalanced quotes etc.): fall back to a text search,
        # but only when something in the command could install a package.
        if re.search(r"\b(npx|bunx|pnpx|uvx|npm|pnpm|yarn|bun|pip[0-9.]*|pipx|uv|claude)\b", command):
            for e in match_text(command, entries):
                add(e)
        return hits

    all_tokens: list[str] = []
    for seg in segments:
        all_tokens += seg
        words = _strip_prefix(seg)
        if not words:
            continue
        head = os.path.basename(words[0]).lower()
        # bash -c "..." and eval "...": look inside.
        if depth < 3 and head in SHELLS and "-c" in words:
            i = words.index("-c")
            if i + 1 < len(words):
                for e in check_command(words[i + 1], entries, depth + 1):
                    add(e)
            continue
        if depth < 3 and head == "eval":
            for e in check_command(" ".join(words[1:]), entries, depth + 1):
                add(e)
            continue
        if is_install(seg):
            for tok in words[1:]:
                add(match_token(tok, entries))
            # claude mcp add-json name '{"command":"npx","args":[...]}'
            if head == "claude":
                for tok in words[1:]:
                    if tok.lstrip().startswith("{"):
                        for e in match_text(tok, entries):
                            add(e)

    # Shapes like `xargs npx` or `find -exec uvx`: a runner appears somewhere
    # and an archived package is named exactly somewhere.
    if any(os.path.basename(t).lower() in RUNNERS for t in all_tokens):
        for tok in all_tokens:
            add(match_token(tok, entries))
    return hits


# ---------------------------------------------------------------- file edits


def is_config_path(file_path: str) -> bool:
    norm = file_path.replace("\\", "/")
    if norm.rsplit("/", 1)[-1] in CONFIG_BASENAMES:
        return True
    return any(p.search(norm) for p in CONFIG_PATTERNS)


def is_allow_file(file_path: str) -> bool:
    norm = file_path.replace("\\", "/")
    return norm.rsplit("/", 1)[-1] == ALLOW_FILE or norm.endswith("/sunsetwatch/allow")


def edit_text(tool_name: str, tool_input: dict[str, Any]) -> str:
    """The text a Write/Edit/MultiEdit call would put into the file."""
    if tool_name == "Write":
        return str(tool_input.get("content", ""))
    if tool_name == "Edit":
        return str(tool_input.get("new_string", ""))
    if tool_name == "MultiEdit":
        return "\n".join(str(e.get("new_string", "")) for e in tool_input.get("edits", []) or [])
    return ""


# ---------------------------------------------------------------- messages


def describe(entry: Entry) -> str:
    r = entry.raw
    since = f" in {r['since']}" if r.get("since") else ""
    lines = [f"- {entry.package} was archived{since} and gets no security fixes."]
    if r.get("risk"):
        lines.append(f"  {r['risk']}")
    if r.get("replacement"):
        lines.append(f"  Use instead: {r['replacement']}")
        if r.get("replacement_docs"):
            lines.append(f"    Docs: {r['replacement_docs']}")
    else:
        lines.append(
            "  No maintained replacement is known. Search the MCP Registry "
            "(https://registry.modelcontextprotocol.io/) or leave the capability off."
        )
    return "\n".join(lines)


def block_message(hits: list[Entry], where: str) -> str:
    body = "\n".join(describe(e) for e in hits)
    names = ", ".join(e.package for e in hits)
    return (
        f"SunsetWatch blocked {where} because it uses an archived MCP server.\n"
        f"{body}\n"
        "Switch to the replacement above, if there is one. Do not retry the archived package under another spelling "
        "or install route. If the user still wants it, tell them; only they can allow it, by adding "
        f"{names} to {ALLOW_FILE} in the project."
    )


ALLOW_FILE_MESSAGE = (
    f"SunsetWatch blocked this change: {ALLOW_FILE} is the user's own allowlist, "
    "so only a person should edit it. Ask the user to make this change themselves."
)


# ---------------------------------------------------------------- hook entry


def evaluate(event: dict[str, Any], entries: list[Entry], allowed: set[str]) -> tuple[str | None, list[Entry]]:
    """Return (reason, hits). reason is None when the call may go ahead."""
    tool = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None, []

    def keep(hits: list[Entry]) -> list[Entry]:
        return [e for e in hits if e.key not in allowed]

    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        if ALLOW_FILE in command and (
            re.search(r">>?\s*[^\s;&|]*" + re.escape(ALLOW_FILE), command)
            or re.search(r"\btee\b|\bsed\b[^|;&]*\s-i|(^|[\s;&|(])(cp|mv|ln|rm|truncate|install)\s", command)
        ):
            return ALLOW_FILE_MESSAGE, []
        hits = keep(check_command(command, entries))
        return (block_message(hits, "this command"), hits) if hits else (None, [])

    if tool in ("Write", "Edit", "MultiEdit"):
        path = str(tool_input.get("file_path", ""))
        if is_allow_file(path):
            return ALLOW_FILE_MESSAGE, []
        if is_config_path(path):
            hits = keep(match_text(edit_text(tool, tool_input), entries))
            if hits:
                return block_message(hits, f"this edit to {os.path.basename(path)}"), hits
    return None, []


def run_hook(stdin_text: str) -> int:
    mode = (os.environ.get(MODE_ENV) or "block").strip().lower()
    if mode == "off":
        return 0
    try:
        event = json.loads(stdin_text or "{}")
    except json.JSONDecodeError:
        return 0  # not a hook event we understand; never break the session
    if not isinstance(event, dict):
        return 0
    try:
        entries = load_entries()
    except (OSError, ValueError, KeyError) as exc:
        sys.stderr.write(f"SunsetWatch guard: could not load the registry ({exc}); not checking.\n")
        return 1  # non-blocking error, shown to the user
    reason, _ = evaluate(event, entries, load_allowed(event.get("cwd")))
    if reason is None:
        return 0
    if mode == "ask":
        out = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason,
            }
        }
        sys.stdout.write(json.dumps(out))
        return 0
    sys.stderr.write(reason + "\n")
    return 2  # Claude Code blocks the tool call and shows stderr to the agent


# ---------------------------------------------------------------- CLI


def main(argv: list[str]) -> int:
    if not argv:
        return run_hook(sys.stdin.read())
    cmd = argv[0]
    entries = load_entries()
    allowed = load_allowed(os.getcwd())
    if cmd == "--list":
        for e in entries:
            sys.stdout.write(f"{e.ecosystem:5} {e.package}  ->  {e.raw.get('replacement') or 'no replacement'}\n")
        return 0
    if cmd == "--check-command" and len(argv) >= 2:
        hits = [e for e in check_command(" ".join(argv[1:]), entries) if e.key not in allowed]
        if hits:
            sys.stdout.write(block_message(hits, "this command") + "\n")
            return 1
        sys.stdout.write("ok: no archived MCP servers in that command\n")
        return 0
    sys.stderr.write(__doc__ or "")
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

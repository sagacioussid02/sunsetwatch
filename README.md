# SunsetWatch — POC

A local proof-of-concept for **SunsetWatch**, an AI model deprecation monitor:
scan a codebase for AI model ID strings, check them against a deprecation
registry, and produce a report of what is retired, what is retiring (and when),
and what is unknown.

Built 2026-11-08 from the validation dossier in
`../files/validations/sunsetwatch.md`. The dossier's verdict was *keep parked*;
this POC exists because Sidd chose to start with SunsetWatch anyway, and its
job is to prove the core loop cheaply before any MVP decision.

## What it proves

- **Scan → match → report works end-to-end.** Point it at a directory; it finds
  model IDs in source files (`.py`, `.ts`, `.js`, `.go`, `.java`, …), config
  files (`.json`, `.yaml`, `.toml`), and `.env` files, with exact `file:line`
  locations.
- **Registry matching handles the dossier's hard cases, not just the easy ones:**
  - exact entries beat family dates (`gpt-4o-2024-05-13` retired 2026-10-23,
    *not* with the other GPT-4o snapshots on 2027-01-14);
  - dated snapshots match family rules (`gpt-4o-2024-08-06` → 2027-01-14);
  - bare aliases are reported as aliases, not given invented dates (`gpt-4o`);
  - per-route dates are carried in the data (Claude Sonnet 4.5: 2026-11-30 on
    the Claude API, 2027-04-08 on Bedrock).
- **Parameter deprecations are flagged too:** `temperature` / `top_p` / `top_k`
  in Anthropic-referencing files, per the Anthropic API version 2026-10-21
  removal.
- **Retired MCP servers are flagged too (added 2026-11-23, v0.2).** MCP
  servers live in client configs (`.mcp.json`, `claude_desktop_config.json`,
  …), not dependency files — and in May 2025 the MCP project moved 14 of its
  reference servers to a read-only archive (`servers-archived`, "no security
  guarantees"). They still install and run via `npx`/`uvx`; they just never
  get fixes or protocol updates, and they're still downloaded ~600K times a
  week. The scan finds `@modelcontextprotocol/server-*` (npm) and
  `mcp-server-*` (PyPI) package references, reports archived ones with their
  maintained replacement where one exists (e.g. `server-github` →
  `github/github-mcp-server`), and reports the 7 still-maintained reference
  servers as `active` so they are never flagged. `--fail-on-risk` also trips
  on archived MCP servers. Source: the Archived section of the
  `modelcontextprotocol/servers` README, checked 2026-11-23.
- **The registry is honest about provenance.** Every date in `registry.json`
  is copied from the 2026-11-08 validation dossier (OpenAI's Oct 23, 2026
  shutdowns, Sonnet 4.5 on Nov 30, 2026, GPT-4o/4.1 snapshots on Jan 14, 2027,
  GPT-5 family on Dec 11, 2026, Gemini 2.5 on Vertex on Oct 20, 2026). No date
  was invented; models not in the seed registry report as `unknown`, which the
  report explicitly says does *not* mean safe.

## Run it

```bash
cd sunsetwatch-poc

# Demo against the bundled fixture project
python3 sunsetwatch.py fixtures/sample-app --out demo-report.md

# Scan a real project (read-only; writes only the report file)
python3 sunsetwatch.py /path/to/project --out report.md

# CI-style: exit 1 if any retired/retiring model is found
python3 sunsetwatch.py /path/to/project --fail-on-risk

# Reproducible runs: pin the scan date
python3 sunsetwatch.py fixtures/sample-app --today 2026-11-08
```

Requirements: Python 3.10+, standard library only. No installs, no network,
no accounts.

## Guard: stop agents installing archived MCP servers (v0.3)

The scan finds archived MCP servers already in a codebase. The guard stops
new ones going in. Coding agents still install reference servers like
`@modelcontextprotocol/server-github` from their training data: Glow Labs'
MCPhantom research (Nov 2026) found that when an agent installed a
deprecated MCP server, the agent chose it itself 98% of the time, and it
reported an unpatched remote code execution flaw in the archived Puppeteer
server. `npx` still installs these packages after a deprecation warning.

`sunsetwatch_guard.py` is a Claude Code `PreToolUse` hook. Before Claude
Code runs a shell command or edits an MCP config file, the hook checks it
against the `mcp_servers` entries in `registry.json` that are `archived`. On
a match it blocks the call and tells the agent the replacement, so the agent
switches on its own:

```
SunsetWatch blocked this command because it uses an archived MCP server.
- @modelcontextprotocol/server-github was archived in 2025-05 and gets no security fixes.
  Use instead: github/github-mcp-server (GitHub's official MCP server)
    Docs: https://github.com/github/github-mcp-server
Switch to the replacement above, if there is one. Do not retry the archived package ...
```

**What it catches**

- Shell installs and runs: `npx`, `bunx`, `pnpx`, `npm install`/`exec`,
  `pnpm add`/`dlx`, `yarn add`/`dlx`, `bun add`, `uvx`, `pip install`,
  `python -m pip install`, `uv pip install`, `uv tool install`,
  `uv run --with`, `pipx run`, and `claude mcp add` / `add-json`. Also inside
  `cd x && ...`, multi-line scripts, `bash -c '...'`, `eval` and `xargs npx`,
  and with `sudo`, `env` or `VAR=value` in front. Version and spelling
  variants count: `pkg@latest`, `npm:pkg`, `--package=pkg`,
  `mcp_server_sqlite`, `MCP-Server-SQLite==2025.4.25`.
- Edits (Write, Edit, MultiEdit) that add an archived server to `.mcp.json`,
  `~/.claude.json`, `claude_desktop_config.json`, Cursor or VS Code
  `mcp.json`, Windsurf `mcp_config.json`, `~/.codex/config.toml`,
  `package.json`, `pyproject.toml` or `requirements*.txt`.
- It leaves alone the `active` reference servers, lookalike names, and
  commands that only read about or remove a package (`npm view`,
  `npm uninstall`, `pip show`, `claude mcp remove`, `grep`).

**Install as a Claude Code plugin (recommended).** In Claude Code, run:

```
/plugin marketplace add sagacioussid02/sunsetwatch
/plugin install sunsetwatch@sunsetwatch
```

Or from a shell: `claude plugin marketplace add sagacioussid02/sunsetwatch`
then `claude plugin install sunsetwatch@sunsetwatch`. The plugin registers
the same `PreToolUse` hook (matcher `Bash|Write|Edit|MultiEdit`) from
`hooks/hooks.json`, with `registry.json` shipped beside the script, so there
is nothing to edit. `/plugin` lets you disable, update or uninstall it
later. Needs `python3` (3.10+) on your `PATH`.

**Or add the hook by hand.** Put it in `~/.claude/settings.json` (every
project) or a repo's `.claude/settings.json`, with the path to your
SunsetWatch checkout. `registry.json` must sit next to the script. Don't do
both, or the guard runs twice.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Write|Edit|MultiEdit",
        "hooks": [
          { "type": "command", "command": "python3 /path/to/sunsetwatch/sunsetwatch_guard.py" }
        ]
      }
    ]
  }
}
```

Check it with
`python3 sunsetwatch_guard.py --check-command "npx -y @modelcontextprotocol/server-github"`
(prints the block message, exits 1), or `--list` for every archived server
it blocks.

**Modes and overrides**

| Setting | Effect |
|---|---|
| `SUNSETWATCH_GUARD_MODE=block` | Default. The call is blocked and the agent sees the replacement. |
| `SUNSETWATCH_GUARD_MODE=ask` | Claude Code asks you to approve the call, with the same message. |
| `SUNSETWATCH_GUARD_MODE=off` | The hook does nothing. |
| `SUNSETWATCH_GUARD_ALLOW=pkg1,pkg2` | Lets these packages through. |
| `.sunsetwatch-allow` | Same, one package per line, `#` comments. Read from the project folder and `~/.config/sunsetwatch/allow`. |
| `SUNSETWATCH_REGISTRY=/path/registry.json` | Use another registry file. |

Set the variables before starting Claude Code; the agent can't change them,
and the hook blocks the agent from editing an allowlist file. If the
registry can't be read, the hook shows an error and lets the call through;
input it doesn't understand also passes. It never breaks the session.

**Limits.** It matches exact package names from the registry, so a renamed
fork, a tarball or `git+https://` install, or a server built by hand gets
through. It sees only what Claude Code's tools do, so run the scan for
configs changed some other way. It covers Claude Code only; Cursor's and
Codex's own agents aren't hooked yet.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

22 scanner tests covering registry matching (exact / family / unknown / alias /
per-route), the scanner (file:line locations, `.env` and config scanning,
skipping `node_modules`/`.git`, dedupe), parameter-deprecation scoping, MCP
server lookups, and an end-to-end fixture scan → report check. 28 guard tests
in `tests/test_guard.py` cover real install command shapes, config edits,
lookalikes and read-only commands that must pass, the allowlist, the hook's
exit codes and ask mode, and that every archived registry entry cites
sources. 4 plugin tests in `tests/test_plugin.py` check the plugin and
marketplace manifests and that `hooks/hooks.json` runs the real guard script
beside `registry.json`. All 54 passing as of 2026-11-24.

## Layout

| Path | What |
|---|---|
| `sunsetwatch.py` | The CLI: scanner + registry + report generator |
| `sunsetwatch_guard.py` | Claude Code hook that blocks installs of archived MCP servers |
| `registry.json` | Hand-seeded lifecycle registry (dossier facts only for models; MCP entries cite their sources) |
| `fixtures/sample-app/` | Fake project with 9 model usages across Python, TypeScript, `.env`, and YAML |
| `demo-report.md` | Output of the demo run against the fixture |
| `tests/test_sunsetwatch.py` | Scanner and registry tests |
| `tests/test_guard.py` | Guard hook tests |
| `.claude-plugin/plugin.json` | Claude Code plugin manifest |
| `.claude-plugin/marketplace.json` | One-plugin marketplace, so `/plugin marketplace add` works on this repo |
| `hooks/hooks.json` | The guard's `PreToolUse` hook, as the plugin installs it |
| `tests/test_plugin.py` | Plugin packaging tests |

## What it deliberately does NOT do

- **No hosted service, no dashboard, no accounts.** The dossier found the only
  possible paid wedge is hosted zero-config convenience; this POC does not
  test that wedge, it tests the scan/report core underneath it.
- **No GitHub integration** — no App, no issues, no PRs. Those are the free
  competitors' ground (llmshift `digest`/`upgrade`, modelbump's Action) and
  stay out of POC scope.
- **No scheduled watching / nightly checks** — it is a point-in-time scan you
  run by hand, like the free CLIs. Continuous monitoring is an MVP question,
  not a POC question.
- **No upgrade PRs or auto-edits** — replacements are reported, never applied.
  The dossier is explicit that provider-named replacements are not
  behavior-equivalent, so a human always makes the swap.
- **No registry maintenance pipeline** — `registry.json` is hand-seeded and
  goes stale. The dossier's #1 risk is exactly this: the registry is a
  treadmill of provider pages that move dates and contradict their own
  emails. Nothing here solves that.
- **No alias-drift detection** (a bare alias silently resolving to a new
  snapshot) — that is modelalive's named feature and far beyond a POC.
- **No notebooks, no AST parsing** — matching is regex over text lines, so
  model-like strings in comments are reported too. Precision work
  (confidence scoring, suppression) is sellable-v1 territory per the dossier.

## The decision this informs

The POC answers "does the scan → registry → report loop work and produce a
report a developer would act on?" — yes, cheaply, in ~350 lines of stdlib
Python. It does **not** answer the question the dossier says actually decides
SunsetWatch's fate: **will teams pay for a hosted, zero-config version when
llmshift / modelbump / modelalive already do scan + digest for free?**

Suggested next step (from the dossier, unchanged): the launch-content
experiment — scan popular public repos for model IDs that died in the
Oct 23, 2026 shutdown and publish the receipts. Sustained attention plus a
waitlist asking for continuous hosted monitoring would justify an MVP
(GitHub App + hosted registry pipeline). Silence — or "I just added the free
Action" — parks SunsetWatch again, having cost a POC instead of seven
weekends.

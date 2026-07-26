# AGENTS.md — AI Agent Quick-Start

## What is hermes-agyx?

A **Hermes Agent plugin** that adds the `agyx` native tool — a coding agent backed
by Google Antigravity (Code Assist) via the `agy` CLI. All capabilities (text,
file read/write, image analysis, image generation, self-healing coding loop) go
through the user's paid agy OAuth login. No Gemini API key needed.

## Quick Install (for Hermes agents)

```bash
git clone https://github.com/lesterppo/hermes-agyx
mkdir -p ~/.hermes/plugins
cp -r hermes-agyx/agyx_plugin ~/.hermes/plugins/agyx_plugin
# Restart Hermes — the agyx tool auto-registers in the 'agy' toolset
```

## Prerequisites for full functionality

1. **`agy` CLI** on PATH, authenticated with paid Google OAuth (`agy -p "hello"`).
2. **Proxy bypass** (for geo-blocked regions): see `skills/agy-bypass/references/bypass-setup.md`.
3. Optional fallback: `GEMINI_API_KEY` env var (used only when agy is absent).

## Key files

| Path | Purpose |
|---|---|
| `agyx_plugin/agyx_tool.py` | Core implementation (single source of truth) |
| `agyx_plugin/agyx_cli.py` | Standalone CLI shim |
| `agyx_plugin/plugin.yaml` | Hermes plugin manifest |
| `agyx_plugin/__init__.py` | Plugin registration entry point |
| `skills/agy-bypass/SKILL.md` | Full skill reference |
| `skills/agy-bypass/references/bypass-setup.md` | Proxy bypass setup (geo-blocked regions) |
| `skills/agy-bypass/references/agyx-testing-recipe.md` | Live verification harness |

## How agyx works (for AI agents)

The `agyx` tool routes ALL capabilities through the `agy` CLI:

```
agyx → agy CLI (paid OAuth, Google Cloud Code API):
  - read=     → text inlined / agy view_file
  - img=      → agy view_file
  - write     → agy write_to_file into out_dir
  - gen=      → agy generate_image (paid)
  - exec=     → agy run_command
  - verify=   → shell check (bounded)
  - auto_fix= → bounded retry loop
  (out_dir diffed before/after → written_files + images)

↳ FALLBACK (only if agy absent AND GEMINI_API_KEY set):
  direct generativelanguage.googleapis.com
```

## Critical pitfalls for agents

1. **agy v1.1.7+ proto format**: v1.1.7 changed `onboardUser` to LRO (Long-Running
   Operation) pattern. Our proxy bypass works with v1.1.5 (188,830,144 bytes).
   If agy fails with "proto: syntax error", downgrade to v1.1.5.

2. **Proxy asymmetry**: chat-only agy needs `HTTPS_PROXY=127.0.0.1:8085`. agyx
   tool calls need proxy vars CLEARED. agyx handles this internally.

3. **Verify before claiming**: exercise every capability through the real auth
   path before telling the user it works. See `agyx-testing-recipe.md`.

4. **Free-tier quota**: image generation on the public API is 429-gated. The agy
   paid path has no such limit.

## Testing

```bash
pip install -e ".[test]"
pytest          # 25+ network-free unit tests
```

For live tests (consumes quota), follow `skills/agy-bypass/references/agyx-testing-recipe.md`.

## License

MIT — see LICENSE.

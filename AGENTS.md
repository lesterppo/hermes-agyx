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

1. **agy v1.1.7+ proto format (UNSOLVED)**: v1.1.7 changed `onboardUser` to LRO
   (Long-Running Operation) pattern returning `google.longrunning.Operation` with
   protobuf-encoded `OnboardUserResponse`. After testing 10+ response formats,
   none matched the exact proto schema. **Use v1.1.5 (188,830,144 bytes) for the
   eligibility bypass.** v1.1.7 is kept as `agy.real.v1.1.7` for future work.

2. **Dual-binary architecture**: The smart wrapper at `~/.local/bin/agy` execs
   v1.1.5 through the proxy bypass. agyx's `which_agy()` prefers the wrapper.
   New `_agy_env()` function preserves proxy vars when using the wrapper (needed
   for eligibility), clears them when using `agy.real` directly.

3. **Proxy asymmetry (FIXED in v2.6.0)**: Previously agyx cleared proxy vars
   for all agy calls, breaking eligibility when the wrapper was selected.
   `_is_wrapper()` + `_agy_env()` now route correctly per binary.

4. **Verify before claiming**: exercise every capability through the real auth
   path before telling the user it works. See `agyx-testing-recipe.md`.

## Testing

```bash
pip install -e ".[test]"
pytest          # 25+ network-free unit tests
```

For live tests (consumes quota), follow `skills/agy-bypass/references/agyx-testing-recipe.md`.

## License

MIT — see LICENSE.

# Making a standalone script a Hermes-native tool (agyx pattern)

Reusable recipe used to turn the standalone `agyx` CLI into the Hermes-native
`agyx` tool (`tools/agyx_tool.py`). Apply this whenever the user says "make it a
Hermes-agent-native tool" for a script in this repo's ecosystem.

## Why native (not just a CLI wrapper)

A native tool is invoked by the agent like `terminal`/`read_file` — schema in
the system prompt, dispatched through `registry.dispatch`, returns JSON. A CLI
wrapper still requires the agent to shell out and parse stdout. Native is the
right call when the capability should be a first-class agent action.

## Steps

1. **Create `tools/<name>_tool.py`** with the full implementation (stdlib only —
   no new deps unless unavoidable). Import from `tools.registry import registry,
   tool_error`. Keep the real logic in a function like `run(...)` that returns a
   dict; the handler wraps it in `json.dumps`.

2. **Register** at module level:
   ```python
   registry.register(
       name="agyx",
       toolset="agy",
       schema=AGYX_SCHEMA,          # flat: name + description + parameters at top
       handler=_handle_agyx,        # fn(args_dict, **kw) -> JSON string
       check_fn=check_requirements, # gated: returns bool; tool hidden if False
       is_async=False,
       emoji="🤖",
   )
   ```
   - Handler signature must accept `**kwargs` (the dispatch path injects
     `task_id=`, `session_id=`, `user_task=`). Use a `lambda args, **kw:` wrapper
     if you want a clean signature, OR have the handler ignore extras.
   - Schema is FLAT (name/description/parameters at top). The registry wraps it
     in `{"type":"function",...}` at get_definitions time. Do NOT double-wrap.
   - `check_fn` keeps footprint at zero when unavailable (e.g. no API key) — so
     adding to `_HERMES_CORE_TOOLS` is safe.
   - NOTE: do NOT set `requires_env=["GEMINI_API_KEY"]` when the primary path is
     the paid `agy` OAuth login — `agy`'s presence is the real gate, with the
     key only a fallback. Gate via `check_fn` returning
     `bool(which_agy()) or bool(get_api_keys())`.

3. **Add the toolset** in `toolsets.py` (`TOOLSETS["agy"] = {"description":...,
   "tools":["agyx"], "includes":[]}`) and add the tool name to
   `_HERMES_CORE_TOOLS` if it should be available by default (gated by check_fn).

4. **Make the old CLI a thin shim** that imports the repo module, so there is a
   single source of truth:
   ```python
   sys.path.insert(0, os.path.expanduser("~/.hermes/hermes-agent"))
   from tools.agyx_tool import agyx_run
   ```
   (If the import fails — e.g. run outside the venv — print a clear error, don't
   duplicate the logic.)

## Verification (REQUIRED before claiming it works)

Do NOT stop at "the module imports." Exercise the real dispatch path. **Critical
gotcha:** `registry.dispatch("agyx", ...)` returns `"Unknown tool: agyx"` unless
the tool's module has been imported AND discovery has run **in the same
process** before the dispatch. The module-level `registry.register(...)` only
fires on import — so you must import it and call `discover_builtin_tools()`
first, or the entry simply isn't in the live registry:

```python
from tools.registry import registry, discover_builtin_tools
import tools.agyx_tool as m          # triggers registry.register(...)
discover_builtin_tools()             # ensures full discovery path is complete
assert registry.get_entry("agyx") is not None
out = registry.dispatch("agyx", {"prompt": "...", "gen": "..."}, task_id="t1")
print(out)  # valid JSON; for gen= must actually produce a file
```

(If you skip the import + `discover_builtin_tools()`, `get_entry` is None or
`dispatch` errors with "Unknown tool" even though the file is correct — a silent
trap that wastes a whole test cycle.)

For capabilities needing paid/3rd-party auth (image gen), exercise them through
the **actual auth path** (e.g. shell out to `agy`), not a mock — otherwise you
silently ship a non-working feature. Mocks are fine only to lock regression
logic for the parse/save code, never to "prove" a network capability.

## agyx-specific routing note (CURRENT — all via paid `agy` OAuth)

This session REVERSED the earlier split. `agyx` now routes **EVERY capability**
through the `agy` CLI using the user's paid OAuth login:

- text, file-read (text inlined / `agy` view_file), image-analysis (`agy`
  view_file), file-write (`agy` write_to_file), code-exec (`agy` run_command via
  the `exec` arg), and image generation (`agy` generate_image) ALL go through
  `agy`. No `GEMINI_API_KEY` is needed for the primary path.
- A **key-only fallback** exists only when `agy` is absent AND
  `GEMINI_API_KEY` is set: the direct public `generativelanguage.googleapis.com`
  endpoint for text/read/write/analysis and (if the key has image quota) image
  generation.
- `run_via_agy()` builds one self-contained instruction, shells out to
  `agy -p`, then diffs `out_dir` before/after to report `written_files` +
  `images`. `exec` appends a `run_command` instruction; `verify`/`auto_fix`
  add a bounded self-healing retry (single pass, no unbounded loop).

**Pitfall — degenerate image fixtures:** `agy`'s `view_file` image-analysis
**fails on tiny/degenerate PNGs** (e.g. a hand-built 32×32 red square) with
"Agent execution terminated due to error", but **works fine on real images**
(proven: it correctly described a 603 KB generated JPEG). If image-analysis
returns that error, retest with a real image before assuming the tool is
broken — the tool correctly surfaces `agy`'s reply either way.

**Pitfall — proxy asymmetry:** the chat-only `agy`+mitmproxy path needs the
proxy ON (`HTTPS_PROXY=127.0.0.1:8085`); the paid `agy` OAuth path
(`run_via_agy` / image gen) needs proxy env CLEARED (`env -u HTTPS_PROXY -u
HTTP_PROXY`), because `agy`'s internal Cloud Code API is paid/OAuth and must not
be routed through the MITM proxy. `agyx` clears proxy vars itself inside
`run_via_agy`.

**Pitfall — verify the REAL path, not a fixture artifact:** when a test reports
failure, distinguish "the capability is broken" from "my test fixture is
degenerate." The 32×32 red.png failure above was a fixture problem, not a tool
bug. Always reproduce with a real artifact before patching the tool.

This "route everything through an authenticated paid CLI, keep a key-only
fallback, diff the output dir to report artifacts" pattern is reusable whenever a
capability is gated behind a paid/3rd-party CLI you already have authenticated.

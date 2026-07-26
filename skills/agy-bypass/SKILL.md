---
name: agy-bypass
description: Drive AGY (Antigravity/Code Assist) via your paid agy OAuth login — Hermes-native agyx tool for text, local file read/write, image analysis, image generation, and a self-healing coding loop.
version: 2.5.0
author: Peter
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [agy, antigravity, bypass, gemini, agyx, image, files]
    category: devops
---

# AGY Bypass (expanded)

`agyx` is a **Hermes-native tool** backed by the user's **paid `agy` OAuth
login** — ALL capabilities (text, file read, image analysis, file write, image
generation, and a self-healing coding loop via `exec`/`verify`/`auto_fix`) are
driven through the `agy` CLI using the paid subscription. No Gemini API key is
required for the primary path.

A **key-only fallback** exists: when `agy` is not installed but a
`GEMINI_API_KEY` is set, the tool falls back to the public Gemini REST API for
text/read/write/analysis and (if the key has image quota) image generation.

## Capabilities matrix

| Capability        | `agyx` (paid agy OAuth)     |
|-------------------|-----------------------------|
| Chat / Q&A        | Yes                         |
| Local file READ   | Yes (`read=`)               |
| Image ANALYSIS    | Yes (`img=`)                |
| Local file WRITE  | Yes (agy write_to_file)     |
| Image GENERATION  | Yes (agy generate_image, paid OAuth) |

## Quick Start — `agyx` as a Hermes-native tool

`agyx` is registered in the `agy` toolset. The agent calls it like any other
tool; args:

- `prompt` (str): the task. For generation, use `gen` instead (prompt may be empty).
- `read` (list[str]): local file path(s) to read into context (text inlined;
  non-text/binary handed to agy's view_file).
- `img` (list[str]): local image path(s) for agy to analyze via view_file.
- `gen` (str): generate an image from this prompt and save it to `out_dir`.
  Uses your **paid `agy` OAuth login** (internal Cloud Code generate_image).
- `exec` (str): a shell command for `agy` to run via its `run_command` tool
  (e.g. `python3 solution.py`). Its output is included in the reply — use for
  build/run/verify steps. This makes `agyx` a real coding-agent loop.
- `verify` (str): a shell command the **tool itself** runs after `agy` finishes
  to check success (non-zero exit = failure). Bounded to `min(timeout,120)s`.
  Only runs if you pass it. Pairs with `auto_fix`.
- `auto_fix` (bool): if `true` and `verify` fails (non-zero), re-prompt `agy`
  with the failure output and re-verify, repeating until `verify` passes or
  `max_fix_rounds` is reached. Bounded — **never** an unbounded loop.
  `result["auto_fixed"]` reports whether the last retry made `verify` pass;
  `result["fix_rounds"]` counts retries taken; `result["rounds"]` counts total
  `agy` calls (initial + retries).
- `max_fix_rounds` (int, default 1, **hard cap 3**): max auto-fix retries when
  `auto_fix=True`. The self-healing loop never exceeds this.
- `watch_dirs` (list[str]): extra directories to watch for created/modified
  files, beyond `out_dir`. Parents of every `read` path are auto-watched. Use
  this when `agy` edits an existing repo OUTSIDE `out_dir` — otherwise those
  edits are invisible in `written_files`.
- `timeout` (int, default 300): per-`agy`-call timeout. Each invocation (incl.
  every auto-fix retry) is bounded by this.
- `out_dir` (str): where written/generated files land (default `./agyx_out`).
- `model` / `img_model` (str): ONLY used by the key-only fallback when `agy`
  is absent; ignored on the primary paid-agy path.

**Auth:** primary path = `agy` CLI paid OAuth (no API key needed). Fallback =
`GEMINI_API_KEY` direct REST when `agy` is not installed. Both are service-gated
(zero schema footprint until one is available).

`think` (bool): enable Gemini thinking. `max_iter` (int): loop cap.

**Structured errors:** `agy` exits 0 even when its agent loop aborts (quota,
auth, "Agent execution terminated due to error"). The tool detects those
sentinels and returns `success:False` with `error` detailing the failure,
instead of masquerading a failed run as success. If `verify` still fails after
all fix rounds, `success:False` is returned with `verify_exit` + last output.

Every result also carries `elapsed_s` (real wall-clock seconds for the task) so
you can watch quota/latency cost. Concurrent `agyx` calls are serialized by a
cross-process lock (agy shares one OAuth/browser profile), so two simultaneous
calls can't corrupt each other's session.

Returns compact JSON: `{"success", "text", "written_files" (incl. out-of-tree
edits via watch_dirs/read parents), "images", "verify_exit", "verify_output",
"rounds", "auto_fixed"/"fix_rounds" (if auto_fix ran), "elapsed_s", "error"}`.

**Self-healing coding loop example** (all via paid `agy`): read buggy code →
`agy` fixes + writes a test → `exec` runs it → `verify` checks exit code → on
failure `auto_fix` asks `agy` to fix once. The shell is only ever executed by
`agy`'s `run_command` (for `exec`) or by the tool's own bounded `verify` — never
by an unbounded internal loop.

## Quick Start — `agyx` CLI (terminal, same module)

```bash
# Text + read a file + analyze an image:
python -m agyx_plugin.agyx_cli "Explain what this script does" \
  --read ./script.py --img ./diagram.png --out-dir ./agyx_out

# Generate an image (saved to --out-dir):
python -m agyx_plugin.agyx_cli "unused" --gen "A blue circle on white" --out-dir ./agyx_out

# Self-healing coding loop:
python -m agyx_plugin.agyx_cli "Fix bugs in ./buggy/calc.py" \
  --read ./buggy/calc.py --verify "cd ./buggy && python3 -m pytest -q" \
  --auto-fix --max-fix-rounds 3
```

## Setup (for a new AI agent / user)

1. **Install Hermes** and ensure `agy` is on PATH, authenticated with your paid
   Google Antigravity / Code Assist OAuth login (`agy` manages its own token).
   Run `agy -p "hello"` once to confirm login works. If `agy` is absent, set a
   `GEMINI_API_KEY` for the key-only fallback.
2. **Install this plugin** (no core edits): copy `agyx_plugin/` into your
   Hermes plugins search path — e.g. `~/.hermes/plugins/agyx_plugin/` — or
   `pip install .` (package `hermes-agyx`) then point Hermes at it. Restart
   Hermes; the `agyx` tool appears in the `agy` toolset, gated by availability.
3. **Verify the tool is registered** and a real path works:
   ```python
   from tools.registry import registry, discover_builtin_tools
   discover_builtin_tools()
   registry.dispatch("agyx", {"prompt": "say hi", "out_dir": "/tmp/agyx_check"})
   ```
   (For the plugin install path, Hermes discovers it automatically — no
   `discover_builtin_tools()` import needed.)
4. **Run a real task** (not a mock) to confirm auth: e.g. call `agyx` with
   `gen="..."` and confirm a valid image file is produced, or `read=` + `exec=`
   + `verify=` on a small repo and confirm `verify_exit == 0`.

> Never report a capability as working until it is exercised through its real
> auth path (live `agy`, or the real key). Mocks prove parse/save logic only.

## Architecture

```
agyx → `agy` CLI (paid OAuth, Google internal Cloud Code API) — PRIMARY PATH:
        - read=      → text inlined / agy view_file
        - img=       → agy view_file on the image
        - write      → agy write_to_file into out_dir
        - gen=       → agy generate_image (paid subscription)
        - exec=      → agy run_command (build/run/verify)
        (out_dir + watch_dirs + read-parents are diffed before/after to report
         written_files + images)
      ↳ FALLBACK (only if agy absent AND GEMINI_API_KEY set):
        - direct generativelanguage.googleapis.com?key=… for text/read/write/analysis
          and (if key has image quota) image generation
```

## Pitfalls

- **Free-tier key quota is tight** — image generation on the public API returns
  **429** (gated behind a paid plan). The `agy` paid path does not have this
  limit. `agyx` waits out 429 windows automatically in the fallback path.
- `agyx` is a Hermes-native tool (registered via `ctx.register_tool` in the
  plugin's `register()`), NOT a core edit. The `agyx_plugin/agyx_cli.py` CLI is
  a thin shim over the same module.
- **All agyx capabilities use your paid `agy` OAuth login** (text, file read,
  image analysis, file write, image generation). No Gemini API key is needed
  for the primary path. A `GEMINI_API_KEY` only enables the key-only fallback.
- `agy` is authenticated via Google OAuth; test paid features by driving `agy`
  itself, not by replaying its token against the public API.
- **VERIFY BEFORE CLAIMING.** Exercise every capability through its real auth
  path before telling the user it works (see references/agyx-testing-recipe.md).
- The tool only edits/creates files you point it at (via out_dir / write_to_file
  and the optional `watch_dirs`). It never touches `~/.hermes` internals.

## References

- `references/agyx-native-tool.md` — turning a standalone script into a
  Hermes-native tool (`registry.register` / plugin `register()` + toolset + thin
  CLI shim + the required dispatch-verification step).
- `references/agyx-testing-recipe.md` — deterministic live-verification harness
  (registration check, dispatch sweep, self-healing coding-loop example) to
  re-prove `agyx` works through the paid `agy` login after any refactor.
- `references/agyx-coding-loop-and-gaps.md` — the exec/verify/auto_fix
  self-healing loop, how to deliberately trigger the auto_fix retry live, the
  in-place-edit detection fix, test-mock signature pitfalls, and resolved gaps
  (structured error surface, out-of-tree diff scope, max_fix_rounds, shared-
  profile lock).
- `references/bypass-setup.md` — privacy-safe proxy bypass setup for
  geo-blocked regions (mitmproxy + systemd + wrapper + GEMINI_API_KEY).
  Includes v1.1.7→v1.1.5 downgrade procedure and all known pitfalls.

## Pitfalls — Geo-block bypass specific

- **agy v1.1.7+** changed `onboardUser` to a Google LRO (Long-Running
  Operation) proto format. The proxy bypass can only match v1.1.5's simpler
  `{"done": true}` format. If agy fails with "proto: syntax error", downgrade
  to v1.1.5 (188,830,144 bytes). See `references/bypass-setup.md`.
- **VPN alone does not bypass** — Google checks **account region** (tied to
  your Google account's registered country), not just IP. The mitmproxy
  bypass faking server-side responses is required.
- **Proxy asymmetry**: agyx tool calls clear proxy vars internally
  (`env -u HTTPS_PROXY -u HTTP_PROXY`). The chat-only `agy` wrapper needs
  the proxy ON. Do not mix the two paths.
